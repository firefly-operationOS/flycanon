# Copyright 2026 Firefly Software Solutions Inc
"""``PgVectorVectorStore`` -- pgvector-backed dense vector projection.

flycanon-side adapter that implements
:class:`fireflyframework_agentic.vectorstores.base.VectorStoreProtocol`
on top of PostgreSQL with the ``pgvector`` extension. Co-locates the
vector projection with the canonical store (Postgres) so production
deployments don't have to operate a separate vector database.

The store creates one table per service deployment
(``canon_chunk_vectors`` by default) with the following shape::

    CREATE TABLE canon_chunk_vectors (
        id           TEXT PRIMARY KEY,
        namespace    TEXT NOT NULL DEFAULT 'default',
        tenant_id    TEXT NOT NULL DEFAULT 'default',
        workspace_id TEXT NOT NULL DEFAULT 'default',
        embedding    vector(<dimensions>) NOT NULL,
        metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
        text         TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX canon_chunk_vectors_hnsw ON canon_chunk_vectors
        USING hnsw (embedding vector_cosine_ops);
    CREATE INDEX canon_chunk_vectors_namespace ON canon_chunk_vectors (namespace);
    CREATE INDEX canon_chunk_vectors_scope
        ON canon_chunk_vectors (tenant_id, workspace_id);

The HNSW index gives sub-millisecond ANN over millions of rows; the
namespace column is retained as a legacy diagnostic, and the
``(tenant_id, workspace_id)`` composite is the canonical scope used by
Plan 3 retrieval filters.

Activated when ``FLYCANON_VECTOR_STORE=pgvector``; requires the
``pgvector`` extra (``uv sync --extra pgvector``) and the pgvector
extension installed on the Postgres instance
(``CREATE EXTENSION IF NOT EXISTS vector``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


# Plan 3 default for ``hnsw.ef_search``. The HNSW index builder picks a
# small candidate-list size by default (~40); 200 trades ~2ms of extra
# query time for recall that's competitive with brute force. ``SET LOCAL``
# scopes the bump to a single transaction, so other workloads on the same
# connection pool are unaffected.
_HNSW_EF_SEARCH = 200

# Default widening factor for the post-fetch ANN window. The query pulls
# ``top_k * widening_factor`` candidates from the ANN index so that the
# downstream cross-encoder / RRF reranker can rescore a wider pool and
# the trim happens server-side instead of in Python.
_DEFAULT_WIDENING_FACTOR = 5


def _scope_namespace(tenant_id: str, workspace_id: str) -> str:
    """Canonical ``t/<tenant>/w/<workspace>`` namespace string.

    Kept as a backstop while the framework's
    :class:`VectorStoreProtocol` still threads ``namespace`` everywhere
    -- the new scope columns are the authoritative filter, but mirroring
    them onto ``namespace`` keeps diagnostics and any non-scope-aware
    caller working.
    """
    return f"t/{tenant_id}/w/{workspace_id}"


class PgVectorVectorStore:
    """Backend-agnostic VectorStoreProtocol implementation over pgvector."""

    def __init__(
        self,
        *,
        database_url: str,
        dimension: int,
        table_name: str = "canon_chunk_vectors",
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 64,
    ) -> None:
        try:
            import pgvector  # noqa: F401  -- import to surface missing-extra failures
        except ImportError as exc:
            raise RuntimeError(
                "pgvector backend requires the ``pgvector`` extra (``uv sync --extra pgvector``)."
            ) from exc

        self._url = _to_async_url(database_url)
        self._dim = dimension
        self._table = table_name
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construction = hnsw_ef_construction
        self._engine: AsyncEngine | None = None
        self._factory: async_sessionmaker | None = None
        self._initialised = False

    async def _ensure_engine(self) -> async_sessionmaker:
        if self._factory is None:
            self._engine = create_async_engine(self._url, future=True, pool_pre_ping=True)
            self._factory = async_sessionmaker(self._engine, expire_on_commit=False)
        if not self._initialised:
            await self._initialise_schema()
            self._initialised = True
        return self._factory

    async def _initialise_schema(self) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id           TEXT PRIMARY KEY,
                        namespace    TEXT NOT NULL DEFAULT 'default',
                        tenant_id    TEXT NOT NULL DEFAULT 'default',
                        workspace_id TEXT NOT NULL DEFAULT 'default',
                        embedding    vector({self._dim}) NOT NULL,
                        metadata     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        text         TEXT NOT NULL,
                        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    f"""
                    CREATE INDEX IF NOT EXISTS {self._table}_hnsw
                    ON {self._table} USING hnsw (embedding vector_cosine_ops)
                    WITH (m = {self._hnsw_m}, ef_construction = {self._hnsw_ef_construction})
                    """
                )
            )
            await conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {self._table}_namespace ON {self._table} (namespace)")
            )
            await conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {self._table}_scope "
                    f"ON {self._table} (tenant_id, workspace_id)"
                )
            )

    # ------------------------------------------------------------------
    # VectorStoreProtocol surface
    # ------------------------------------------------------------------

    async def upsert(
        self,
        documents: list[Any],
        namespace: str = "default",
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> None:
        """Persist documents with scope columns + canonical namespace.

        ``tenant_id`` / ``workspace_id`` are the authoritative scope --
        the legacy ``namespace`` argument is preserved for callers that
        haven't been migrated to the scoped API yet but is overridden
        whenever the canonical ``t/<tenant>/w/<workspace>`` template
        applies.
        """
        factory = await self._ensure_engine()
        scope_namespace = _scope_namespace(tenant_id, workspace_id)
        rows = [
            _doc_to_row(
                doc,
                namespace=scope_namespace if namespace == "default" else namespace,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            for doc in documents
        ]
        if not rows:
            return
        async with factory() as session, session.begin():
            # Bulk upsert via INSERT ... ON CONFLICT (id) DO UPDATE.
            # All values are parametrized; only the table identifier is
            # interpolated (it's controlled by configuration, not user
            # input).
            await session.execute(
                text(
                    f"""
                        INSERT INTO {self._table}
                            (id, namespace, tenant_id, workspace_id, embedding, metadata, text)
                        VALUES
                            (:id, :namespace, :tenant_id, :workspace_id,
                             :embedding, CAST(:metadata AS jsonb), :text)
                        ON CONFLICT (id) DO UPDATE
                        SET namespace    = EXCLUDED.namespace,
                            tenant_id    = EXCLUDED.tenant_id,
                            workspace_id = EXCLUDED.workspace_id,
                            embedding    = EXCLUDED.embedding,
                            metadata     = EXCLUDED.metadata,
                            text         = EXCLUDED.text
                        """
                ),
                rows,
            )

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        namespace: str = "default",
        filters: list[Any] | None = None,
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
        widening_factor: int = _DEFAULT_WIDENING_FACTOR,
    ) -> list[Any]:
        """ANN search filtered to ``(tenant_id, workspace_id)`` scope.

        The query runs inside an explicit transaction that bumps
        ``hnsw.ef_search`` for higher recall, then filters on the
        scope columns. ``LIMIT`` is widened server-side by
        ``widening_factor`` (default 5) so that the downstream
        cross-encoder / RRF reranker can rescore a larger pool;
        the result list is trimmed to ``top_k`` before returning.
        """
        from fireflyframework_agentic.vectorstores.types import SearchResult, VectorDocument

        factory = await self._ensure_engine()
        embedding_literal = _vector_literal(query_embedding)
        widened_limit = max(1, top_k * widening_factor)
        async with factory() as session, session.begin():
            # ``SET LOCAL`` is per-transaction and only valid on
            # Postgres. The pgvector adapter is Postgres-only at
            # construction time (we import the ``pgvector`` extra
            # in ``__init__``), so the dialect check below is
            # belt-and-suspenders for tests that swap in a SQLite
            # engine via monkeypatching.
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "postgresql":
                await session.execute(text(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
            result = await session.execute(
                text(
                    f"""
                    SELECT id, namespace, metadata, text,
                           1 - (embedding <=> :query_embedding) AS score
                    FROM {self._table}
                    WHERE tenant_id = :tenant_id
                      AND workspace_id = :workspace_id
                    ORDER BY embedding <=> :query_embedding
                    LIMIT :limit
                    """
                ),
                {
                    "query_embedding": embedding_literal,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "limit": widened_limit,
                },
            )
            rows = result.all()

        hits = [
            SearchResult(
                document=VectorDocument(
                    id=str(row.id),
                    text=row.text,
                    embedding=None,
                    metadata=dict(row.metadata or {}),
                    namespace=row.namespace,
                ),
                score=float(row.score),
            )
            for row in rows
        ]
        # Trim post-fetch -- the SQL ``LIMIT`` widens the candidate
        # pool for the downstream reranker; here we cap to the
        # caller's requested top_k.
        return hits[:top_k]

    async def search_text(
        self,
        query: str,
        top_k: int = 5,
        namespace: str = "default",
        filters: list[Any] | None = None,
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> list[Any]:
        raise NotImplementedError(
            "pgvector adapter requires a precomputed embedding; use search(query_embedding=...) instead."
        )

    async def delete(
        self,
        ids: list[str],
        namespace: str = "default",
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> None:
        if not ids:
            return
        factory = await self._ensure_engine()
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    f"DELETE FROM {self._table} "
                    f"WHERE tenant_id = :tenant_id "
                    f"  AND workspace_id = :workspace_id "
                    f"  AND id = ANY(:ids)"
                ),
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "ids": list(ids),
                },
            )

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._factory = None


def _doc_to_row(
    doc: Any,
    *,
    namespace: str,
    tenant_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    import json

    embedding = list(doc.embedding or [])
    if not embedding:
        raise ValueError(f"VectorDocument {doc.id!r} has no embedding; pgvector requires one")
    return {
        "id": str(doc.id),
        "namespace": getattr(doc, "namespace", None) or namespace,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "embedding": _vector_literal(embedding),
        "metadata": json.dumps(dict(getattr(doc, "metadata", {}) or {})),
        "text": getattr(doc, "text", "") or "",
    }


def _vector_literal(values: list[float]) -> str:
    """Render a Python list as a pgvector input literal."""
    return "[" + ",".join(f"{float(v):.7f}" for v in values) + "]"


def _to_async_url(database_url: str) -> str:
    """Coerce any SQLAlchemy URL to its asyncpg variant for pgvector use."""
    if "+asyncpg" in database_url:
        return database_url
    if database_url.startswith("postgresql+psycopg"):
        return database_url.replace("+psycopg", "+asyncpg", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url
