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

"""``RlsPgVectorVectorStore`` -- pgvector dense projection with RLS and embedding sets.

The generic pgvector adapter ships in the framework
(:class:`fireflyframework_agentic.vectorstores.PgVectorVectorStore`): asyncpg,
namespace-scoped, with an HNSW cosine index over a ``vector(N)`` column.
flycanon co-locates the dense projection with the canonical Postgres instance
and adds two things on top that cannot generalize to a framework-level adapter:

**Postgres Row-Level Security.** :meth:`_create_schema` installs an idempotent,
namespace-keyed policy (``USING namespace = current_setting('app.scope_namespace')``),
``FORCE``\\ d so even the table owner is subject to it, and
:meth:`_prepare_session` sets that GUC transaction-locally from the scope
namespace :class:`TenantScopedVectorStore` already encodes. An unset GUC
matches no rows (fail-safe): the table is never reachable unscoped.

**Embedding sets.** Since 26.8.0 the ``embedding`` column has no typmod and
every row carries ``set_id`` / ``dim`` / ``model`` (migration ``0017``), so one
table holds several embedding spaces at once and one chunk can hold a vector in
two of them. :meth:`_upsert` and :meth:`_search` are therefore overridden: the
framework emits ``embedding <=> $1`` with no cast and no set predicate, which
on a mixed table is either an error or the wrong index. The set comes from the
:mod:`flycanon.core.services.embeddings.embedding_sets` ContextVar, bound by
the caller -- there is nowhere on ``upsert(documents, namespace)`` to pass one.

What was deleted, and what replaced it
--------------------------------------
:meth:`_create_schema` used to refuse to boot when the column's width
disagreed with ``FLYCANON_EMBEDDING_DIMENSIONS`` -- "the dimension is fixed
when the table is created and a different width needs a fresh database". After
0017 the column has no width for it to check, and the guarantee it was making
is made better by three other things: pgvector raises on a mis-scoped query
(``expected 768 dimensions, not 3``) rather than answering it wrongly; the
``canon_chunk_vectors_set_coherence`` trigger refuses a vector whose model
disagrees with its set's, which is the failure the width check never caught;
and a set that does not match this process's configuration is a WARNING naming
``flycanon reindex``, because one process legitimately serves workspaces on
several sets.

:meth:`_create_schema` stays VERIFY-FIRST: when the table exists it runs no DDL
at all, so the serving process can run as a role with neither ``CREATE`` on the
schema nor ownership of the table -- PostgreSQL checks the schema privilege on
``CREATE TABLE IF NOT EXISTS`` and demands ownership on ``CREATE INDEX IF NOT
EXISTS`` BEFORE it consults the guard (measured by the dworkers programme on
2026-09-17).

Activated when ``FLYCANON_VECTOR_STORE=pgvector`` (the default). Requires the
``pgvector`` extension on the Postgres server.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.exceptions import VectorStoreError
from fireflyframework_agentic.vectorstores import PgVectorVectorStore
from fireflyframework_agentic.vectorstores.pgvector_store import (
    _filter_clause,
    _load_metadata,
    _vector_literal,
)
from fireflyframework_agentic.vectorstores.types import SearchFilter, SearchResult, VectorDocument

from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetBinding, current_embedding_set
from flycanon.models.entities.embedding_set import ann_cast, index_name_for

logger = logging.getLogger(__name__)


def _asyncpg_dsn(database_url: str) -> str:
    """Coerce a SQLAlchemy URL to the plain DSN asyncpg accepts.

    flycanon's ``database_url`` is the SQLAlchemy ``postgresql+asyncpg://`` form;
    ``asyncpg.create_pool`` wants a driverless ``postgresql://`` DSN.
    """
    for marker in ("+asyncpg", "+psycopg2", "+psycopg"):
        database_url = database_url.replace(marker, "", 1)
    return database_url


def vector_table_ddl(table: str) -> list[str]:
    """The embedding-set shape of the dense projection, in execution order.

    Migration ``0017`` is what creates this in every real deployment; this is
    the lazy path for a stack that boots against a database no migration has
    touched (a dev box, a fresh test container). The ANN indexes are NOT here:
    there is one per embedding set and the first is built when the first set
    is written.
    """
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id         TEXT NOT NULL,
            set_id     TEXT NOT NULL,
            namespace  TEXT NOT NULL DEFAULT 'default',
            embedding  vector NOT NULL,
            dim        INTEGER NOT NULL,
            model      TEXT NOT NULL,
            text       TEXT NOT NULL,
            metadata   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (set_id, id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS {table}_namespace ON {table} (namespace)",
        f"CREATE INDEX IF NOT EXISTS {table}_set ON {table} (set_id)",
    ]


class RlsPgVectorVectorStore(PgVectorVectorStore):
    """pgvector dense store + flycanon namespace-keyed RLS + embedding sets."""

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
        # Set ids whose partial ANN index this process has already tried to
        # create. One attempt per set per process: the statement is idempotent
        # but it is also a round trip, and on a non-owner role it warns.
        self._indexed_sets: set[str] = set()

    # -- the embedding set in force ----------------------------------------

    def _binding(self) -> EmbeddingSetBinding:
        """The set this operation belongs to. Missing is a programmer error.

        Fail loud rather than defaulting: writing a vector into a guessed set,
        or searching one, is precisely the silent corruption embedding sets
        exist to make impossible.
        """
        binding = current_embedding_set()
        if binding is None:
            raise VectorStoreError(
                f"no embedding set is bound for this {self._table} operation. Every read and write "
                "belongs to one embedding space: callers resolve it with EmbeddingSetService and "
                "hold it open with bind_embedding_set(...)."
            )
        return binding

    @staticmethod
    def _width(binding: EmbeddingSetBinding) -> int:
        """The width to cast to, validated because it is interpolated into SQL.

        The value comes from a NOT NULL INTEGER column with a
        ``BETWEEN 64 AND 4096`` CHECK, so this can only fire on a hand-built
        binding -- which is exactly when a guard is worth having.
        """
        width = int(binding.dimensions)
        if not 64 <= width <= 4096:
            raise VectorStoreError(
                f"embedding set {binding.set_id} declares {width} dimensions, outside the "
                "supported range 64..4096"
            )
        return width

    # -- schema ------------------------------------------------------------

    async def _create_schema(self, conn: Any) -> None:
        existing = await conn.fetchrow(
            """
            SELECT EXISTS (
                       SELECT 1 FROM pg_attribute a2
                       WHERE a2.attrelid = c.oid AND a2.attname = 'set_id' AND a2.attnum > 0
                   ) AS has_set_id,
                   format_type(a.atttypid, a.atttypmod) AS column_type,
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
            # The table is there (migration 0017, or a previous boot as the
            # owner): verify, never create. The one thing that cannot be
            # repaired at runtime is a table still in the pre-26.8.0 shape --
            # the process would write vectors with no set and the reindex verb
            # would have nothing to route on -- so it is refused here, with
            # the command that fixes it.
            if not existing["has_set_id"]:
                raise VectorStoreError(
                    f"{self._table} is in the pre-26.8.0 single-width shape "
                    f"({existing['column_type']} with no set_id column). Run `flycanon migrate` "
                    "to apply migration 0017, which relaxes the column, adopts the vectors already "
                    "there into an embedding set and rebuilds the ANN index per set. The migration "
                    "is lossless and reversible."
                )
            if existing["has_policy"]:
                return
            # Table without the policy: an older deployment created it before
            # the policy step existed. Fall through to the DO block below,
            # which installs it when this role owns the table and warns when
            # it does not.
        else:
            for statement in vector_table_ddl(self._table):
                await conn.execute(statement)
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

    # -- the ANN index, one per set ---------------------------------------

    def index_statement(self, binding: EmbeddingSetBinding) -> str:
        """The partial expression HNSW that serves ``binding``.

        pgvector refuses a plain HNSW on an untyped ``vector`` column
        (``column does not have dimensions``); the cast in the index
        expression is what gives it one, and above 2000 dimensions that cast
        has to be ``halfvec`` (see :func:`ann_cast`). The predicate is the
        SET, not the width: two sets at the same width -- a re-embed onto the
        same model after a bad batch -- must not share an index, or a switch
        degenerates into filtered ANN across both and under-recalls exactly
        when correctness matters most.
        """
        width = self._width(binding)
        cast, ops = ann_cast(width)
        return (
            f"CREATE INDEX IF NOT EXISTS {index_name_for(binding.set_id, table=self._table)} "
            f"ON {self._table} USING hnsw ((embedding::{cast}({width})) {ops}) "
            f"WITH (m = {self._hnsw_m}, ef_construction = {self._hnsw_ef_construction}) "
            f"WHERE set_id = '{binding.set_id}'"
        )

    async def ensure_set_index(self, binding: EmbeddingSetBinding, *, conn: Any | None = None) -> bool:
        """Build ``binding``'s ANN index if this role may. Returns whether it exists.

        ``CREATE INDEX`` demands ownership of the table, which the serving
        ``flycanon_app`` role does not have. Rather than fail an ingest over
        it, this warns with the exact statement an operator has to run -- the
        same shape the RLS policy install already uses. ``flycanon reindex``
        runs with admin credentials and therefore takes the happy path; it
        also refuses to ACTIVATE a set whose index it could not build, so an
        unindexed set never quietly becomes the one that answers queries.
        """
        if binding.set_id in self._indexed_sets:
            return True
        statement = self.index_statement(binding)
        pool = await self._ensure_pool()
        try:
            if conn is not None:
                await conn.execute(statement)
            else:
                async with pool.acquire() as own:
                    await own.execute(statement)
        except Exception as exc:  # asyncpg raises InsufficientPrivilegeError among others
            logger.warning(
                "could not create the ANN index for embedding set %s (%s). Searches on this set "
                "run without an index until an owner runs:\n    %s",
                binding.set_id,
                exc,
                statement,
            )
            return False
        self._indexed_sets.add(binding.set_id)
        logger.info(
            "ANN index ready for embedding set %s (%s @%d)",
            binding.set_id,
            binding.embedding_model,
            binding.dimensions,
        )
        return True

    async def drop_set(self, set_id: str, *, namespace: str) -> int:
        """Delete every vector of ``set_id`` in ``namespace`` and its index.

        The index drop is attempted after the rows, and a role that may not
        drop it gets a warning rather than a failure: the rows are what cost
        storage, and an index over zero rows is an inconvenience.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._prepare_session(conn, namespace=namespace)
            deleted = await conn.execute(
                f"DELETE FROM {self._table} WHERE set_id = $1 AND namespace = $2", set_id, namespace
            )
        index = index_name_for(set_id, table=self._table)
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP INDEX IF EXISTS "{index}"')
        except Exception as exc:
            logger.warning("could not drop the ANN index %s (%s); run it as an owner", index, exc)
        self._indexed_sets.discard(set_id)
        # asyncpg returns the command tag, e.g. ``DELETE 217``.
        return int(str(deleted).rsplit(" ", 1)[-1] or 0)

    # -- VectorStoreProtocol surface ---------------------------------------

    async def _upsert(self, documents: list[VectorDocument], namespace: str) -> None:
        """Write into the bound set, keyed ``(set_id, id)``.

        ``ON CONFLICT (set_id, id) DO UPDATE`` is what makes a replayed
        reindex batch idempotent -- replaying rewrites the same rows rather
        than duplicating them or colliding with the other set's copy of the
        same chunk.
        """
        binding = self._binding()
        width = self._width(binding)
        rows = []
        for doc in documents:
            if doc.embedding is None:
                raise VectorStoreError(f"VectorDocument {doc.id!r} has no embedding; pgvector requires one.")
            if len(doc.embedding) != width:
                # The provider ignored ``dimensions=`` or returned a short
                # vector. Before 26.8.0 this surfaced as an opaque INSERT
                # error from the column typmod; the column has no typmod now,
                # so the check has to be here or a wrong-width row would land
                # in the set and break its index build.
                raise VectorStoreError(
                    f"embedding set {binding.set_id} is {binding.embedding_model} @{width} but the "
                    f"embedder returned {len(doc.embedding)} dimensions for chunk {doc.id!r}. The "
                    "provider ignored the requested width; nothing has been written."
                )
            rows.append(
                (
                    doc.id,
                    binding.set_id,
                    namespace,
                    _vector_literal(doc.embedding),
                    width,
                    binding.embedding_model,
                    doc.text,
                    json.dumps(doc.metadata),
                )
            )
        if not rows:
            return
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._prepare_session(conn, namespace=namespace)
            await conn.executemany(
                f"""
                INSERT INTO {self._table}
                       (id, set_id, namespace, embedding, dim, model, text, metadata)
                VALUES ($1, $2, $3, $4::vector, $5, $6, $7, $8::jsonb)
                ON CONFLICT (set_id, id) DO UPDATE
                SET namespace = EXCLUDED.namespace,
                    embedding = EXCLUDED.embedding,
                    dim       = EXCLUDED.dim,
                    model     = EXCLUDED.model,
                    text      = EXCLUDED.text,
                    metadata  = EXCLUDED.metadata
                """,
                rows,
            )
        await self.ensure_set_index(binding)

    async def _search(
        self,
        query_embedding: list[float],
        top_k: int,
        namespace: str,
        filters: list[SearchFilter] | None,
    ) -> list[SearchResult]:
        """ANN over ONE set, with the cast the partial index is built on.

        The ``set_id`` predicate and the ``::vector(N)`` cast have to match the
        index expression character for character or the planner falls back to a
        sequential scan -- and on a table holding two widths a query without
        the predicate does not merely scan slowly, it raises
        ``expected N dimensions, not M``. That error is the replacement for the
        boot-time width refusal: a mis-scoped query is a failure, not a wrong
        answer, and pgvector enforces it rather than our code.
        """
        binding = self._binding()
        width = self._width(binding)
        if len(query_embedding) != width:
            raise VectorStoreError(
                f"the query was embedded to {len(query_embedding)} dimensions but embedding set "
                f"{binding.set_id} is {binding.embedding_model} @{width}. A query must be embedded "
                "by the model that produced the corpus it searches."
            )
        cast, _ops = ann_cast(width)
        params: list[Any] = [_vector_literal(query_embedding), namespace, top_k, binding.set_id]
        where = ["namespace = $2", "set_id = $4"]
        next_index = 5
        for f in filters or []:
            clause, clause_params, next_index = _filter_clause(f, next_index)
            where.append(clause)
            params.extend(clause_params)
        sql = f"""
            SELECT id, text, metadata,
                   1 - (embedding::{cast}({width}) <=> $1::{cast}({width})) AS score
            FROM {self._table}
            WHERE {" AND ".join(where)}
            ORDER BY embedding::{cast}({width}) <=> $1::{cast}({width})
            LIMIT $3
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._prepare_session(conn, namespace=namespace)
            await conn.execute(f"SET LOCAL hnsw.ef_search = {self._hnsw_ef_search}")
            # The namespace predicate is applied AFTER the ANN scan, so a small
            # workspace inside a large set can under-recall at the default
            # ``off``. Relaxed-order iterative scan lets pgvector widen the
            # search until the filter is satisfied, which is what makes the
            # per-workspace filter safe on a shared table.
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            records = await conn.fetch(sql, *params)
        return [
            SearchResult(
                document=VectorDocument(
                    id=str(rec["id"]),
                    text=rec["text"],
                    embedding=None,
                    metadata=_load_metadata(rec["metadata"]),
                    namespace=namespace,
                ),
                score=float(rec["score"]),
            )
            for rec in records
        ]

    async def _delete(self, ids: list[str], namespace: str) -> None:
        """Purge ``ids`` from EVERY set in ``namespace``.

        A source delete is an erasure claim, and a vector left behind in a
        retired or still-building set is a document the operator believes is
        gone and the RLM corpus can still read. The write path is set-scoped;
        the delete path deliberately is not.
        """
        if not ids:
            return
        pool = await self._ensure_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._prepare_session(conn, namespace=namespace)
            await conn.execute(
                f"DELETE FROM {self._table} WHERE namespace = $1 AND id = ANY($2::text[])",
                namespace,
                [str(i) for i in ids],
            )
