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

"""``RlsPgVectorVectorStore`` -- pgvector dense projection with Postgres RLS.

The generic pgvector adapter ships in the framework
(:class:`fireflyframework_agentic.vectorstores.PgVectorVectorStore`): asyncpg,
namespace-scoped, with an HNSW cosine index. flycanon co-locates the dense
projection with the canonical Postgres instance and adds a second, DB-enforced
isolation layer on top of the application-level namespace scoping: Postgres
Row-Level Security.

This subclass keeps ~all of the adapter upstream and adds only the RLS coupling
that cannot generalize to a framework-level adapter:

* :meth:`_create_schema` installs an idempotent, namespace-keyed RLS policy on
  the vector table (``USING namespace = current_setting('app.scope_namespace')``),
  ``FORCE``\\ d so even the table owner is subject to it.
* :meth:`_prepare_session` sets that GUC, transaction-locally, from the scope
  namespace the :class:`TenantScopedVectorStore` wrapper already encodes -- so
  every read/write/delete runs under the matching RLS predicate. An unset GUC
  matches no rows (fail-safe): the table is never reachable unscoped.
* :meth:`_create_schema` is VERIFY-FIRST: when the table already exists (created
  by migration ``0016`` or an earlier boot) it runs no DDL at all and only checks
  that the column width matches ``FLYCANON_EMBEDDING_DIMENSIONS``. That is what
  lets the serving process run as a role with neither ``CREATE`` on the schema
  nor ownership of the table -- PostgreSQL checks the schema privilege on
  ``CREATE TABLE IF NOT EXISTS`` and demands ownership on ``CREATE INDEX IF NOT
  EXISTS`` BEFORE it consults the guard, so the framework's idempotent DDL is not
  idempotent for a ``NOBYPASSRLS`` application role (measured by the dworkers
  programme on 2026-09-17: the first ingest died with a permission error and the
  policy step logged "install via admin role").

Activated when ``FLYCANON_VECTOR_STORE=pgvector`` (the default). Requires the
``pgvector`` extension on the Postgres server.
"""

from __future__ import annotations

import logging
from typing import Any

from fireflyframework_agentic.exceptions import VectorStoreError
from fireflyframework_agentic.vectorstores import PgVectorVectorStore

logger = logging.getLogger(__name__)


def _asyncpg_dsn(database_url: str) -> str:
    """Coerce a SQLAlchemy URL to the plain DSN asyncpg accepts.

    flycanon's ``database_url`` is the SQLAlchemy ``postgresql+asyncpg://`` form;
    ``asyncpg.create_pool`` wants a driverless ``postgresql://`` DSN.
    """
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        database_url = database_url.replace(marker, "", 1)
    return database_url


class RlsPgVectorVectorStore(PgVectorVectorStore):
    """pgvector dense store + flycanon namespace-keyed Postgres RLS."""

    def __init__(
        self,
        *,
        database_url: str,
        dimension: int,
        table_name: str = "canon_chunk_vectors",
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 64,
        hnsw_ef_search: int = 200,
    ) -> None:
        super().__init__(
            _asyncpg_dsn(database_url),
            dimension=dimension,
            table_name=table_name,
            hnsw_m=hnsw_m,
            hnsw_ef_construction=hnsw_ef_construction,
            hnsw_ef_search=hnsw_ef_search,
        )

    async def _create_schema(self, conn: Any) -> None:
        existing = await conn.fetchrow(
            """
            SELECT format_type(a.atttypid, a.atttypmod) AS column_type,
                   EXISTS (
                       SELECT 1 FROM pg_policy p
                       WHERE p.polrelid = c.oid AND p.polname = 'tenant_workspace_isolation'
                   ) AS has_policy
            FROM   pg_class c
            JOIN   pg_namespace n ON n.oid = c.relnamespace
            JOIN   pg_attribute a ON a.attrelid = c.oid AND a.attname = 'embedding'
            WHERE  n.nspname = current_schema() AND c.relname = $1 AND c.relkind IN ('r', 'p')
            """,
            self._table,
        )
        if existing is not None:
            # The table is there (migration 0016, or a previous boot as the
            # owner): verify, never create. A width mismatch is the one thing
            # that cannot be repaired at runtime -- the column was sized at
            # first boot and a different embedding model needs a fresh
            # database -- so it is refused here, before the first ingest
            # writes a vector the ANN index cannot compare.
            expected = f"vector({self._dimension})"
            if existing["column_type"] != expected:
                raise VectorStoreError(
                    f"{self._table}.embedding is {existing['column_type']} but "
                    f"FLYCANON_EMBEDDING_DIMENSIONS asks for {expected}; the dimension is fixed "
                    "when the table is created and a different width needs a fresh database "
                    "(see docs/deployment.md, 'Embedding dimension')."
                )
            if existing["has_policy"]:
                return
            # Table without the policy: an older deployment created it before
            # the policy step existed. Fall through to the DO block below,
            # which installs it when this role owns the table and warns when
            # it does not.
        else:
            await super()._create_schema(conn)
        # Install the RLS policy in-band with table creation so the table is
        # never reachable from the application without scope -- closes the
        # deploy-ordering gap where a migration's ``IF EXISTS`` guard no-ops on
        # a fresh deploy. The DO block is idempotent (skips if the policy
        # already exists) and soft-fails on insufficient_privilege so a
        # non-admin boot logs a warning instead of crashing.
        await conn.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = '{self._table}'
                      AND policyname = 'tenant_workspace_isolation'
                ) THEN
                    BEGIN
                        EXECUTE 'ALTER TABLE {self._table} ENABLE ROW LEVEL SECURITY';
                        EXECUTE 'ALTER TABLE {self._table} FORCE ROW LEVEL SECURITY';
                        EXECUTE $POLICY$
                            CREATE POLICY tenant_workspace_isolation ON {self._table}
                              USING (namespace = current_setting('app.scope_namespace', true))
                              WITH CHECK (namespace = current_setting('app.scope_namespace', true))
                        $POLICY$;
                    EXCEPTION WHEN insufficient_privilege THEN
                        RAISE WARNING
                            'Insufficient privilege to apply RLS on %; install via admin role.',
                            '{self._table}';
                    END;
                END IF;
            END
            $$;
            """
        )

    async def _prepare_session(self, conn: Any, *, namespace: str) -> None:
        # Transaction-local GUC consumed by the RLS policy above. ``set_config``
        # (unlike ``SET LOCAL``) takes the value as a bind parameter.
        await conn.execute("SELECT set_config('app.scope_namespace', $1, true)", namespace)
