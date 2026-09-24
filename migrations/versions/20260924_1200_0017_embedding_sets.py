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

"""Make the embedding model and its width first-class: embedding sets.

Revision ID: 0017_embedding_sets
Revises: 0016_boot_created_tables
Create Date: 2026-09-24

Postgres-only. SQLite (used by unit tests) gets the two portable ALTERs and
skips everything that needs pgvector.

Until 26.8.0 the width of ``canon_chunk_vectors.embedding`` WAS the width of
the whole deployment: migration ``0016`` baked ``vector(N)`` into the column
from the migrate job's ``FLYCANON_EMBEDDING_DIMENSIONS``, and
:meth:`RlsPgVectorVectorStore._create_schema` refused to boot when a process
disagreed -- "the dimension is fixed when the table is created and a different
width needs a fresh database". A change of embedder was a schema fight, and a
change of MODEL at the same width was not even that: it passed every check in
the system and silently degraded recall, with no error and no log line,
because no row recorded what had produced it.

This migration removes both, by making the embedding space a first-class
entity rather than a property of a column:

* ``canon_embedding_sets`` -- one row per (workspace, embedder configuration).
  RLS under migration 0013's standard ``(tenant_id, workspace_id)`` family.
* ``canon_workspaces.active_embedding_set_id`` -- the set that answers this
  workspace's searches. NULL means "the process default". Changing the
  embedder is one UPDATE of this column; undoing it is the same UPDATE.
* ``canon_chunk_vectors.embedding`` loses its typmod (``vector(N)`` ->
  ``vector``) and gains ``set_id`` / ``dim`` / ``model``, re-keyed
  ``PRIMARY KEY (set_id, id)`` so one chunk can hold a vector in two sets at
  once -- which is what makes a re-embed a batch job with a rollback instead
  of a fresh database. The one global HNSW index is replaced by one PARTIAL
  EXPRESSION index per set, ``USING hnsw ((embedding::vector(N)) ...) WHERE
  set_id = '<id>'``, which pgvector builds and the planner uses.
* ``canon_ingest_jobs.kind`` -- ``ingest`` (every existing row) or
  ``reindex``. The source-shaped columns are already nullable, so a reindex
  job inherits status, attempts, timestamps, correlation id, callback and the
  append-only event stream rather than duplicating them in a second table.
* ``canon_chunks.embedding`` is DROPPED. Nothing in ``src/`` ever wrote it,
  it was NULL on every row of every corpus that exists, and
  ``StatsService._chunk_stats`` counted it -- so the admin dashboard reported
  0.0% embedded on a fully embedded corpus. The statistic now counts
  ``canon_chunk_vectors`` for the workspace's active set.

What the deployment gains, in one sentence: a mis-scoped query is now an
ERROR raised by pgvector (``expected 768 dimensions, not 3``) instead of a
wrong answer, and the schema has no dependency on the migrate job's
environment at all -- 0016 read ``get_settings()`` to SIZE a column, which is
why a migrate/serve env mismatch created one width and then failed every
pod's boot; 0017 reads settings only to LABEL the set it adopts.

Adoption, and why it cannot silently lose a row
-----------------------------------------------
Existing vectors are adopted into one set per namespace present in the table
(``t/<tenant>/w/<workspace>`` decodes to both scope halves), labelled with the
migrate job's ``FLYCANON_EMBEDDING_MODEL`` and sized by ``vector_dims()`` --
measured per row, never assumed. The migrate role is the BYPASSRLS owner
(``docs/deployment.md``, "RLS roles"); a role that does NOT bypass RLS would
see an empty table through the ``FORCE``\\ d namespace policy and adopt
nothing, and the ``SET NOT NULL`` that follows would then fail on the rows it
could not see -- constraint validation reads storage, not the policy. Loud,
not lossy.

Downgrade restores the ``vector(N)`` column when the table holds ONE width,
and REFUSES when it holds more than one rather than truncating rows into a
width they were not embedded at. The refusal names ``flycanon reindex
--drop-set``, which is how a deployment gets back to one width on purpose.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

from flycanon.config import get_settings
from flycanon.models.entities.embedding_set import config_fingerprint, index_name_for, new_embedding_set_id

revision = "0017_embedding_sets"
down_revision = "0016_boot_created_tables"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _table_exists(name: str) -> bool:
    row = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = current_schema() AND c.relname = '{name}' AND c.relkind IN ('r', 'p')"
        )
        .first()
    )
    return row is not None


def embedding_sets_ddl() -> list[str]:
    """The registry table, its indexes and its RLS policy, in execution order.

    Rendered by a function so the shape test can read it without a database.
    The policy is migration 0013's standard scoped family, character for
    character -- ``USING`` only, which PostgreSQL also applies as the
    ``WITH CHECK`` on INSERT.
    """
    return [
        """
        CREATE TABLE IF NOT EXISTS canon_embedding_sets (
            id                 VARCHAR(64) PRIMARY KEY,
            tenant_id          VARCHAR(64)  NOT NULL,
            workspace_id       VARCHAR(64)  NOT NULL,
            provider           VARCHAR(64)  NOT NULL,
            model              VARCHAR(256) NOT NULL,
            dimensions         INTEGER      NOT NULL,
            status             VARCHAR(24)  NOT NULL,
            config_fingerprint VARCHAR(64)  NOT NULL,
            chunk_count        INTEGER      NOT NULL DEFAULT 0,
            vector_count       INTEGER      NOT NULL DEFAULT 0,
            index_name         VARCHAR(128),
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
            ready_at           TIMESTAMPTZ,
            activated_at       TIMESTAMPTZ,
            retired_at         TIMESTAMPTZ,
            created_by         VARCHAR(128),
            note               TEXT,
            CONSTRAINT ck_canon_embedding_sets_dimensions
                CHECK (dimensions BETWEEN 64 AND 4096)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_canon_embedding_sets_tenant_id ON canon_embedding_sets (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_canon_embedding_sets_workspace_id "
        "ON canon_embedding_sets (workspace_id)",
        "CREATE INDEX IF NOT EXISTS ix_canon_embedding_sets_status ON canon_embedding_sets (status)",
        "CREATE INDEX IF NOT EXISTS ix_canon_embedding_sets_tenant_workspace "
        "ON canon_embedding_sets (tenant_id, workspace_id)",
        "ALTER TABLE canon_embedding_sets ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE canon_embedding_sets FORCE ROW LEVEL SECURITY",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = 'canon_embedding_sets'
                  AND policyname = 'tenant_workspace_isolation'
            ) THEN
                EXECUTE $POLICY$
                    CREATE POLICY tenant_workspace_isolation ON canon_embedding_sets
                      USING (
                        tenant_id = current_setting('app.tenant_id', true)
                        AND workspace_id = current_setting('app.workspace_id', true)
                      )
                $POLICY$;
            END IF;
        END
        $$;
        """,
    ]


def partial_hnsw_ddl(*, set_id: str, dimensions: int, hnsw_m: int, hnsw_ef_construction: int) -> str:
    """The ANN index that serves ONE embedding set.

    A partial EXPRESSION index: pgvector refuses a plain HNSW on an untyped
    ``vector`` column (``column does not have dimensions``), and the cast in
    the index expression is what gives it one. The predicate is ``set_id``
    rather than ``dim`` on purpose -- two sets at the same width (a re-embed
    onto the same model) must not share one index, or a switch degenerates
    into filtered ANN across both and under-recalls exactly when it matters.
    """
    index = index_name_for(set_id)
    return (
        f"CREATE INDEX IF NOT EXISTS {index} ON canon_chunk_vectors "
        f"USING hnsw ((embedding::vector({dimensions})) vector_cosine_ops) "
        f"WITH (m = {hnsw_m}, ef_construction = {hnsw_ef_construction}) "
        f"WHERE set_id = '{set_id}'"
    )


def set_coherence_ddl() -> list[str]:
    """The guard that closes the silent-corruption hole, in execution order.

    Before 26.8.0, changing ``FLYCANON_EMBEDDING_MODEL`` to another model of
    the SAME width passed every check in the system: the column was
    ``vector(768)``, the store booted, ingestion wrote new-model vectors
    alongside old-model ones, and the query stage cosine-compared a query in
    one embedding space against a corpus in another. Silently degraded recall,
    no error, no log line -- the exact trap waiting for any deployment moving
    from a 768-wide local embedder to a 768-wide hosted one.

    After this trigger it is structurally impossible: every vector row names
    its set, every set names its embedder, and a row whose ``(model, dim)``
    disagrees with its set's is refused by the database with a message naming
    the command that does the thing the writer was trying to do.

    SECURITY DEFINER because the lookup must succeed whatever scope the
    writer is under -- a vector whose set cannot be found is itself the bug
    this is here to catch, so "not found" raises rather than waving through.
    """
    return [
        """
        CREATE OR REPLACE FUNCTION canon_chunk_vectors_set_coherence()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $FN$
        DECLARE
            declared RECORD;
        BEGIN
            SELECT provider, model, dimensions
              INTO declared
              FROM canon_embedding_sets
             WHERE id = NEW.set_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'canon_chunk_vectors.set_id = % names no row in canon_embedding_sets; '
                    'every vector belongs to a declared embedding set '
                    '(see `flycanon reindex --list`)', NEW.set_id;
            END IF;
            IF NEW.model IS DISTINCT FROM (declared.provider || ':' || declared.model)
               OR NEW.dim IS DISTINCT FROM declared.dimensions THEN
                RAISE EXCEPTION
                    'embedding set % is %:% @%, and a vector from % @% was written into it. '
                    'An embedding set is ONE embedding space: mixing models inside one set '
                    'degrades recall silently instead of failing. Build a new set: '
                    'flycanon reindex --workspace % --to % --dimensions %',
                    NEW.set_id, declared.provider, declared.model, declared.dimensions,
                    NEW.model, NEW.dim,
                    (SELECT workspace_id FROM canon_embedding_sets WHERE id = NEW.set_id),
                    NEW.model, NEW.dim;
            END IF;
            RETURN NEW;
        END;
        $FN$
        """,
        "DROP TRIGGER IF EXISTS canon_chunk_vectors_set_coherence ON canon_chunk_vectors",
        """
        CREATE TRIGGER canon_chunk_vectors_set_coherence
        BEFORE INSERT OR UPDATE ON canon_chunk_vectors
        FOR EACH ROW EXECUTE FUNCTION canon_chunk_vectors_set_coherence()
        """,
    ]


def _adopt_existing_vectors(bind: sa.Connection) -> None:
    """Fold every vector already in the table into one set per namespace.

    On every deployment that exists other than the dworkers dev stack this
    adopts nothing and is pure DDL. It still ships, and is still tested,
    because it is the only thing standing between a future populated
    deployment and a fresh database.
    """
    settings = get_settings()
    provider, _, model = settings.embedding_model.partition(":")
    provider = provider or "unknown"
    model = model or settings.embedding_model
    endpoint = settings.azure_openai_endpoint if provider.startswith("azure") else ""

    namespaces = bind.execute(
        sa.text(
            """
            SELECT namespace,
                   max(vector_dims(embedding)) AS max_dim,
                   min(vector_dims(embedding)) AS min_dim,
                   count(*)                    AS rows
            FROM   canon_chunk_vectors
            GROUP  BY namespace
            """
        )
    ).all()
    if not namespaces:
        logger.info("0017: canon_chunk_vectors is empty -- nothing to adopt")
        return

    for row in namespaces:
        namespace = str(row.namespace)
        if int(row.max_dim) != int(row.min_dim):
            # Impossible through 0016's vector(N) column, and a refusal rather
            # than a guess if some other path ever produced it.
            raise RuntimeError(
                f"0017: namespace {namespace!r} holds vectors of {row.min_dim} and {row.max_dim} "
                "dimensions; adoption needs one width per namespace. Drop the odd rows and re-run."
            )
        # ``t/<tenant_id>/w/<workspace_id>`` -- the canonical scope namespace.
        parts = namespace.split("/")
        tenant_id = parts[1] if len(parts) > 3 and parts[0] == "t" else namespace
        workspace_id = parts[3] if len(parts) > 3 and parts[2] == "w" else namespace
        dimensions = int(row.max_dim)
        set_id = new_embedding_set_id()
        bind.execute(
            sa.text(
                """
                INSERT INTO canon_embedding_sets (
                    id, tenant_id, workspace_id, provider, model, dimensions, status,
                    config_fingerprint, chunk_count, vector_count, index_name, activated_at, note
                ) VALUES (
                    :id, :tenant_id, :workspace_id, :provider, :model, :dimensions, 'active',
                    :fingerprint, :rows, :rows, :index_name, now(), :note
                )
                """
            ),
            {
                "id": set_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "provider": provider,
                "model": model,
                "dimensions": dimensions,
                "fingerprint": config_fingerprint(
                    provider=provider,
                    model=model,
                    dimensions=dimensions,
                    endpoint=endpoint,
                    api_version=settings.azure_openai_api_version if provider.startswith("azure") else "",
                ),
                "rows": int(row.rows),
                "index_name": index_name_for(set_id),
                "note": "adopted by migration 0017 from the pre-26.8.0 single-width table",
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE canon_chunk_vectors
                SET    set_id = :set_id,
                       dim    = vector_dims(embedding),
                       model  = :embedding_model
                WHERE  namespace = :namespace
                """
            ),
            {
                "set_id": set_id,
                "embedding_model": f"{provider}:{model}",
                "namespace": namespace,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE canon_workspaces SET active_embedding_set_id = :set_id "
                "WHERE id = :workspace_id AND tenant_id = :tenant_id"
            ),
            {"set_id": set_id, "workspace_id": workspace_id, "tenant_id": tenant_id},
        )
        logger.info(
            "0017: adopted %d vector(s) of namespace %s into set %s (%s:%s @%d)",
            int(row.rows),
            namespace,
            set_id,
            provider,
            model,
            dimensions,
        )


def upgrade() -> None:
    # Portable on both dialects: the reindex job discriminator, the active-set
    # pointer, and the removal of the column that made the dashboard lie.
    op.add_column(
        "canon_ingest_jobs",
        sa.Column("kind", sa.String(24), nullable=False, server_default=sa.text("'ingest'")),
    )
    op.create_index("ix_canon_ingest_jobs_kind", "canon_ingest_jobs", ["kind"])
    op.add_column("canon_workspaces", sa.Column("active_embedding_set_id", sa.String(64), nullable=True))
    op.drop_column("canon_chunks", "embedding")

    if not _is_postgres():
        # SQLite carries the ORM's own ``canon_embedding_sets`` from
        # ``Base.metadata.create_all``; there is no pgvector to reshape.
        return

    for statement in embedding_sets_ddl():
        op.execute(statement)

    if not _table_exists("canon_chunk_vectors"):
        logger.warning(
            "0017: canon_chunk_vectors does not exist (a server without the `vector` extension, "
            "or FLYCANON_VECTOR_STORE != pgvector); the dense projection is created in the "
            "embedding-set shape when the store first boots"
        )
        return

    bind = op.get_bind()
    settings = get_settings()

    # The global HNSW has to go before the typmod does: pgvector cannot hold
    # an index on a column with no dimensions, so the ALTER would fail with
    # the index in place.
    op.execute("DROP INDEX IF EXISTS canon_chunk_vectors_hnsw")
    op.execute("ALTER TABLE canon_chunk_vectors ADD COLUMN IF NOT EXISTS set_id TEXT")
    op.execute("ALTER TABLE canon_chunk_vectors ADD COLUMN IF NOT EXISTS dim INTEGER")
    op.execute("ALTER TABLE canon_chunk_vectors ADD COLUMN IF NOT EXISTS model TEXT")
    # A typmod relaxation: it rewrites no value and cannot drop a row.
    op.execute("ALTER TABLE canon_chunk_vectors ALTER COLUMN embedding TYPE vector USING embedding")

    _adopt_existing_vectors(bind)

    op.execute("ALTER TABLE canon_chunk_vectors ALTER COLUMN set_id SET NOT NULL")
    op.execute("ALTER TABLE canon_chunk_vectors ALTER COLUMN dim SET NOT NULL")
    op.execute("ALTER TABLE canon_chunk_vectors ALTER COLUMN model SET NOT NULL")

    # Re-key so the same chunk can hold a vector in two sets at once. This is
    # not cosmetic: ``ON CONFLICT (set_id, id) DO UPDATE`` is what makes a
    # replayed reindex batch idempotent.
    op.execute("ALTER TABLE canon_chunk_vectors DROP CONSTRAINT IF EXISTS canon_chunk_vectors_pkey")
    op.execute("ALTER TABLE canon_chunk_vectors ADD PRIMARY KEY (set_id, id)")
    op.execute("CREATE INDEX IF NOT EXISTS canon_chunk_vectors_set ON canon_chunk_vectors (set_id)")

    # Installed after adoption so the adoption's own UPDATE is not asked to
    # satisfy a guard about rows it is in the middle of labelling.
    for statement in set_coherence_ddl():
        op.execute(statement)

    for adopted in bind.execute(sa.text("SELECT id, dimensions FROM canon_embedding_sets ORDER BY id")).all():
        op.execute(
            partial_hnsw_ddl(
                set_id=str(adopted.id),
                dimensions=int(adopted.dimensions),
                hnsw_m=settings.pgvector_hnsw_m,
                hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
            )
        )


def downgrade() -> None:
    if _is_postgres() and _table_exists("canon_chunk_vectors"):
        bind = op.get_bind()
        widths = [int(r[0]) for r in bind.execute(sa.text("SELECT DISTINCT dim FROM canon_chunk_vectors"))]
        if len(widths) > 1:
            raise RuntimeError(
                "0017 downgrade: canon_chunk_vectors holds vectors of "
                f"{sorted(widths)} dimensions and a vector(N) column can hold one width. "
                "Reduce to a single set first -- `flycanon reindex --list`, then "
                "`flycanon reindex --drop-set <set-id>` for every set but the active one -- "
                "and run the downgrade again. Nothing has been changed."
            )
        for index in bind.execute(
            sa.text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'canon_chunk_vectors' AND indexname LIKE 'canon_chunk_vectors_hnsw%'"
            )
        ).scalars():
            op.execute(f'DROP INDEX IF EXISTS "{index}"')
        op.execute("DROP INDEX IF EXISTS canon_chunk_vectors_set")
        op.execute("ALTER TABLE canon_chunk_vectors DROP CONSTRAINT IF EXISTS canon_chunk_vectors_pkey")
        settings = get_settings()
        width = widths[0] if widths else settings.embedding_dimensions
        op.execute(
            f"ALTER TABLE canon_chunk_vectors ALTER COLUMN embedding TYPE vector({width}) "
            f"USING embedding::vector({width})"
        )
        op.execute("ALTER TABLE canon_chunk_vectors ADD PRIMARY KEY (id)")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS canon_chunk_vectors_hnsw ON canon_chunk_vectors "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = {settings.pgvector_hnsw_m}, "
            f"ef_construction = {settings.pgvector_hnsw_ef_construction})"
        )
        op.execute("DROP TRIGGER IF EXISTS canon_chunk_vectors_set_coherence ON canon_chunk_vectors")
        op.execute("DROP FUNCTION IF EXISTS canon_chunk_vectors_set_coherence()")
        op.execute("ALTER TABLE canon_chunk_vectors DROP COLUMN IF EXISTS set_id")
        op.execute("ALTER TABLE canon_chunk_vectors DROP COLUMN IF EXISTS dim")
        op.execute("ALTER TABLE canon_chunk_vectors DROP COLUMN IF EXISTS model")

    if _is_postgres():
        op.execute("DROP TABLE IF EXISTS canon_embedding_sets CASCADE")

    op.add_column("canon_chunks", sa.Column("embedding", sa.JSON(), nullable=True))
    op.drop_column("canon_workspaces", "active_embedding_set_id")
    op.drop_index("ix_canon_ingest_jobs_kind", table_name="canon_ingest_jobs")
    op.drop_column("canon_ingest_jobs", "kind")
