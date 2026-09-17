# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Create the three tables the processes used to create at boot.

Revision ID: 0016_boot_created_tables
Revises: 0015_source_object_store_key
Create Date: 2026-09-17

Postgres-only. SQLite (used by unit tests) is a no-op.

Until 26.7.1 three tables in flycanon's database were created by NOTHING
in this history:

* ``canon_chunk_vectors`` -- the pgvector dense projection. The framework's
  :class:`PgVectorVectorStore` created it lazily (extension, table, HNSW
  and namespace indexes) in whichever process first touched the pool, and
  flycanon's :class:`RlsPgVectorVectorStore` added the ``FORCE``\\ d
  namespace-keyed RLS policy in the same step. Migration ``0013`` guards
  its own ``ALTER`` with ``IF EXISTS`` for exactly that reason.
* ``pyfly_eda_outbox`` and ``pyfly_eda_offsets`` -- PyFly's
  :class:`PostgresEventBus` runs their ``CREATE TABLE IF NOT EXISTS`` and
  ``CREATE INDEX IF NOT EXISTS`` in ``start()``, in the API and in the
  worker alike.

In a stack where every process is the database owner none of that is
visible. In a shared, multi-tenant deployment the serving process runs as
a ``NOBYPASSRLS`` role with ``USAGE`` on the schema and DML on the tables
(``docs/deployment.md``, "RLS roles") -- and PostgreSQL then refuses the
lazy path twice over: ``CREATE TABLE IF NOT EXISTS`` checks ``CREATE`` on
the schema before it notices the table exists, and ``CREATE INDEX IF NOT
EXISTS`` demands ownership of the table before it consults the guard
(both measured by the dworkers programme on 2026-09-17 against a
``flycanon_app`` role). The first ingest died with a permission error that
read like a bug in the ingest, and the store's policy step logged
"install via admin role" and carried on with an unprotected table.

So this migration creates all three, once, as the migration role (the
BYPASSRLS owner), BEFORE any process boots:

* The vector table is created in the framework's namespace shape --
  ``(id, namespace, embedding, text, metadata, created_at)`` -- with the
  HNSW cosine index, the namespace index and the RLS policy, sized by
  ``FLYCANON_EMBEDDING_DIMENSIONS`` and the ``FLYCANON_PGVECTOR_HNSW_*``
  settings read from the environment of the migrate job. THE DIMENSION IS
  LOCKED HERE: ``vector(N)`` is baked into the column at this moment, and
  a later switch to an embedding model of another width needs a fresh
  database. :meth:`RlsPgVectorVectorStore._create_schema` is verify-first
  since 26.7.1 -- it runs no DDL when the table exists and refuses to boot
  on a width mismatch -- so what is created here is what the processes
  find. The DDL is restated rather than imported because the framework
  exposes it only as f-strings inside ``_create_schema``;
  ``tests/unit/test_migration_0016_boot_created_tables.py`` holds the two
  column sets equal so they cannot drift silently.
* The outbox tables come from PyFly's own DDL constants (``_DDL_OUTBOX``,
  ``_DDL_OFFSETS``), byte for byte, so they can never differ from what
  ``start()`` would have created. PyFly still runs that DDL at boot (an
  upstream ask: skip it when the tables exist); on a database prepared
  here the ``IF NOT EXISTS`` guards pass for the OWNER, and a deployment
  that serves as a non-owner grants that role ``CREATE`` on the schema and
  membership of the owning role until PyFly ships the skip -- see
  ``docs/deployment.md``.

Without the ``vector`` extension available on the server (a stock
``postgres`` image, as the BM25-only unit tests use) the vector table is
skipped with a warning: the dense store is then created lazily as before,
which is the pre-26.7.1 behaviour on a server that could not run pgvector
anyway. The outbox tables need no extension and are always created.

Downgrade drops only ``canon_chunk_vectors``: it is a DERIVED projection
(``canon_chunks`` is the system of record) that a re-index rebuilds. The
outbox tables hold undelivered events and are left alone; PyFly recreates
them if they are ever dropped by hand.
"""

from __future__ import annotations

import logging

from alembic import op
from pyfly.eda.adapters.postgres import _DDL_OFFSETS, _DDL_OUTBOX

from flycanon.config import get_settings

revision = "0016_boot_created_tables"
down_revision = "0015_source_object_store_key"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _vector_extension_available() -> bool:
    row = op.get_bind().exec_driver_sql("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'").first()
    return row is not None


def vector_table_ddl(*, table: str, dimension: int, hnsw_m: int, hnsw_ef_construction: int) -> list[str]:
    """The statements that create the dense projection, in execution order.

    Kept as a function of the settings so the drift test can render them
    for any width and compare the column set with the framework's
    :meth:`PgVectorVectorStore._create_schema`.
    """
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id         TEXT PRIMARY KEY,
            namespace  TEXT NOT NULL DEFAULT 'default',
            embedding  vector({dimension}) NOT NULL,
            text       TEXT NOT NULL,
            metadata   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {table}_hnsw
        ON {table} USING hnsw (embedding vector_cosine_ops)
        WITH (m = {hnsw_m}, ef_construction = {hnsw_ef_construction})
        """,
        f"CREATE INDEX IF NOT EXISTS {table}_namespace ON {table} (namespace)",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = '{table}'
                  AND policyname = 'tenant_workspace_isolation'
            ) THEN
                EXECUTE $POLICY$
                    CREATE POLICY tenant_workspace_isolation ON {table}
                      USING (namespace = current_setting('app.scope_namespace', true))
                      WITH CHECK (namespace = current_setting('app.scope_namespace', true))
                $POLICY$;
            END IF;
        END
        $$;
        """,
    ]


def upgrade() -> None:
    if not _is_postgres():
        return

    # PyFly's outbox, from PyFly's own DDL. ``op.execute`` of a multi-statement
    # string is fine on psycopg: the constants are plain ``;``-separated DDL.
    op.execute(_DDL_OUTBOX)
    op.execute(_DDL_OFFSETS)

    settings = get_settings()
    if settings.vector_store != "pgvector":
        logger.info(
            "0016: FLYCANON_VECTOR_STORE=%s -- the dense projection lives elsewhere; only the outbox is made",
            settings.vector_store,
        )
        return
    if not _vector_extension_available():
        logger.warning(
            "0016: the `vector` extension is not available on this server; %s is not created here and "
            "will be created lazily by the dense store at boot (which needs the extension anyway)",
            settings.pgvector_table,
        )
        return
    for statement in vector_table_ddl(
        table=settings.pgvector_table,
        dimension=settings.embedding_dimensions,
        hnsw_m=settings.pgvector_hnsw_m,
        hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
    ):
        op.execute(statement)


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"DROP TABLE IF EXISTS {get_settings().pgvector_table} CASCADE")
