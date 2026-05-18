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
        id          TEXT PRIMARY KEY,
        namespace   TEXT NOT NULL DEFAULT 'default',
        embedding   vector(<dimensions>) NOT NULL,
        metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
        text        TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX canon_chunk_vectors_hnsw ON canon_chunk_vectors
        USING hnsw (embedding vector_cosine_ops);
    CREATE INDEX canon_chunk_vectors_namespace ON canon_chunk_vectors (namespace);

The HNSW index gives sub-millisecond ANN over millions of rows; the
namespace column lets the same table host multiple logical
collections (e.g. one per tenant).

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
                        id          TEXT PRIMARY KEY,
                        namespace   TEXT NOT NULL DEFAULT 'default',
                        embedding   vector({self._dim}) NOT NULL,
                        metadata    JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        text        TEXT NOT NULL,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
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

    # ------------------------------------------------------------------
    # VectorStoreProtocol surface
    # ------------------------------------------------------------------

    async def upsert(
        self,
        documents: list[Any],
        namespace: str = "default",
    ) -> None:
        factory = await self._ensure_engine()
        rows = [_doc_to_row(doc, namespace) for doc in documents]
        if not rows:
            return
        async with factory() as session, session.begin():
            # Bulk upsert via INSERT ... ON CONFLICT (id) DO UPDATE.
            await session.execute(
                text(
                    f"""
                        INSERT INTO {self._table} (id, namespace, embedding, metadata, text)
                        VALUES (:id, :namespace, :embedding, CAST(:metadata AS jsonb), :text)
                        ON CONFLICT (id) DO UPDATE
                        SET namespace = EXCLUDED.namespace,
                            embedding = EXCLUDED.embedding,
                            metadata  = EXCLUDED.metadata,
                            text      = EXCLUDED.text
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
    ) -> list[Any]:
        from fireflyframework_agentic.vectorstores.types import SearchResult, VectorDocument

        factory = await self._ensure_engine()
        embedding_literal = _vector_literal(query_embedding)
        async with factory() as session:
            result = await session.execute(
                text(
                    f"""
                    SELECT id, namespace, metadata, text,
                           1 - (embedding <=> :query_embedding) AS score
                    FROM {self._table}
                    WHERE namespace = :namespace
                    ORDER BY embedding <=> :query_embedding
                    LIMIT :top_k
                    """
                ),
                {
                    "query_embedding": embedding_literal,
                    "namespace": namespace,
                    "top_k": top_k,
                },
            )
            return [
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
                for row in result
            ]

    async def search_text(
        self,
        query: str,
        top_k: int = 5,
        namespace: str = "default",
        filters: list[Any] | None = None,
    ) -> list[Any]:
        raise NotImplementedError(
            "pgvector adapter requires a precomputed embedding; use search(query_embedding=...) instead."
        )

    async def delete(self, ids: list[str], namespace: str = "default") -> None:
        if not ids:
            return
        factory = await self._ensure_engine()
        async with factory() as session, session.begin():
            await session.execute(
                text(f"DELETE FROM {self._table} WHERE namespace = :namespace AND id = ANY(:ids)"),
                {"namespace": namespace, "ids": list(ids)},
            )

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._factory = None


def _doc_to_row(doc: Any, namespace: str) -> dict[str, Any]:
    import json

    embedding = list(doc.embedding or [])
    if not embedding:
        raise ValueError(f"VectorDocument {doc.id!r} has no embedding; pgvector requires one")
    return {
        "id": str(doc.id),
        "namespace": getattr(doc, "namespace", None) or namespace,
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
