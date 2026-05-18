# Copyright 2026 Firefly Software Solutions Inc
"""``PostgresCorpus`` -- BM25 over ``canon_chunks`` via tsvector + GIN.

Implements the surface that the agentic ``HybridRetriever`` consumes
on top of the canonical Postgres ``canon_chunks`` table -- the same
table the rest of flycanon writes via :class:`ChunkRepository`. The
BM25 projection rides on a GENERATED ``tsv`` column populated
automatically by Postgres (see migration ``0003_bm25_tsv``); flycanon
never has to maintain it.

Methods called by the agentic retriever:

* :meth:`bm25_search`  -- returns the top-k chunks for a free-text
  query, ranked by ``ts_rank_cd``.
* :meth:`get_chunks`   -- hydrates a list of chunk ids into the
  ``StoredChunk`` shape the retriever's RRF pass expects.

Methods used by :class:`IndexService` to keep the corpus interface
stable across backends:

* :meth:`upsert_chunks`     -- no-op (canon_chunks is the canonical
  store; the ``tsv`` projection is maintained by the GENERATED
  column).
* :meth:`delete_by_doc_id`  -- no-op (the source-id FK cascades the
  delete to ``canon_chunks``).
* :meth:`initialise` / :meth:`close` -- no-ops (Alembic owns the
  schema lifecycle).

This module never reads or writes ``canon_chunk_vectors`` -- the
dense projection lives on :class:`PgVectorVectorStore`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fireflyframework_agentic.rag.corpus import ChunkHit, StoredChunk
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)


class PostgresCorpus:
    """Agentic-corpus-compatible BM25 surface on ``canon_chunks``."""

    def __init__(self, database_url: str, *, search_config: str = "simple") -> None:
        # Reuse the async engine pattern from the rest of flycanon --
        # one engine per repository / corpus, pooled by asyncpg
        # itself.
        self._database_url = database_url
        self._search_config = search_config
        self._engine: AsyncEngine | None = None
        self._lock = asyncio.Lock()

    async def _ensure_engine(self) -> AsyncEngine:
        if self._engine is None:
            async with self._lock:
                if self._engine is None:
                    self._engine = create_async_engine(
                        self._database_url, pool_pre_ping=True
                    )
        return self._engine

    # ------------------------------------------------------------------
    # Lifecycle (no-ops -- the schema is owned by Alembic)
    # ------------------------------------------------------------------

    async def initialise(self) -> None:
        await self._ensure_engine()

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # Writes (no-ops -- ChunkRepository owns canon_chunks; the tsv
    # column is GENERATED so the BM25 projection follows automatically).
    # ------------------------------------------------------------------

    async def upsert_chunks(self, chunks: Any) -> None:  # noqa: ARG002
        return None

    async def delete_by_doc_id(self, doc_id: str) -> int:  # noqa: ARG002
        # ``canon_chunks.source_id`` has ON DELETE CASCADE on the FK
        # to ``canon_sources``; per-source clean-up happens via
        # :class:`ChunkRepository.replace_for_source`.
        return 0

    async def clear_all(self) -> None:
        engine = await self._ensure_engine()
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM canon_chunks"))

    # ------------------------------------------------------------------
    # Reads -- the surface the HybridRetriever consumes
    # ------------------------------------------------------------------

    async def bm25_search(self, query: str, *, top_k: int = 30) -> list[ChunkHit]:
        """Return the top-``top_k`` chunks ranked by Postgres BM25.

        Uses ``plainto_tsquery`` for the user query (safe vs. raw
        text) and ``ts_rank_cd`` for the score. ``ts_rank_cd``
        boosts term density better than ``ts_rank`` -- closer to
        traditional BM25 semantics. Hits with empty/whitespace
        queries return ``[]`` rather than scanning the whole table.
        """
        q = (query or "").strip()
        if not q:
            return []
        engine = await self._ensure_engine()
        sql = text(
            """
            SELECT
                c.id          AS chunk_id,
                c.source_id   AS doc_id,
                c.content     AS content,
                c.section_path,
                c.page,
                c.metadata_json,
                COALESCE(s.filename, s.uri, s.id) AS source_path,
                ts_rank_cd(c.tsv, plainto_tsquery(:cfg, :q)) AS score
            FROM canon_chunks AS c
            JOIN canon_sources AS s ON s.id = c.source_id
            WHERE c.tsv @@ plainto_tsquery(:cfg, :q)
            ORDER BY score DESC, c.created_at DESC
            LIMIT :k
            """
        )
        async with engine.connect() as conn:
            result = await conn.execute(
                sql, {"cfg": self._search_config, "q": q, "k": int(top_k)}
            )
            rows = result.mappings().all()

        hits: list[ChunkHit] = []
        for row in rows:
            metadata: dict[str, Any] = dict(row["metadata_json"] or {})
            if row["section_path"]:
                metadata.setdefault("section_path", str(row["section_path"]))
            if row["page"] is not None:
                # Stored as string so the downstream agentic
                # ``ChunkHit.metadata`` shape (dict[str, str]) stays
                # honest -- callers cast back to int when they need
                # the numeric value (the DTO mapper does this).
                metadata.setdefault("page", str(row["page"]))
            hits.append(
                ChunkHit(
                    chunk_id=row["chunk_id"],
                    score=float(row["score"] or 0.0),
                    content=row["content"] or "",
                    metadata=metadata,
                    source_path=row["source_path"] or "",
                    doc_id=row["doc_id"] or "",
                )
            )
        return hits

    async def get_chunks(self, chunk_ids: list[str]) -> list[StoredChunk]:
        """Hydrate a list of chunk ids into ``StoredChunk`` rows.

        The retriever uses this after RRF fusion to fetch the body
        text + provenance for the chosen ids. Order is preserved to
        match the input list so the fusion ranks line up with the
        hydrated rows.
        """
        if not chunk_ids:
            return []
        engine = await self._ensure_engine()
        sql = text(
            """
            SELECT
                c.id          AS chunk_id,
                c.source_id   AS doc_id,
                c.content     AS content,
                c.index_in_source,
                c.section_path,
                c.page,
                c.metadata_json,
                COALESCE(s.filename, s.uri, s.id) AS source_path
            FROM canon_chunks AS c
            JOIN canon_sources AS s ON s.id = c.source_id
            WHERE c.id = ANY(:ids)
            """
        )
        async with engine.connect() as conn:
            result = await conn.execute(sql, {"ids": list(chunk_ids)})
            rows = {row["chunk_id"]: row for row in result.mappings().all()}

        ordered: list[StoredChunk] = []
        for chunk_id in chunk_ids:
            row = rows.get(chunk_id)
            if row is None:
                continue
            metadata: dict[str, Any] = dict(row["metadata_json"] or {})
            if row["section_path"]:
                metadata.setdefault("section_path", row["section_path"])
            if row["page"] is not None:
                metadata.setdefault("page", row["page"])
            ordered.append(
                StoredChunk(
                    chunk_id=row["chunk_id"],
                    doc_id=row["doc_id"],
                    source_path=row["source_path"] or "",
                    index_in_doc=int(row["index_in_source"] or 0),
                    content=row["content"] or "",
                    metadata=metadata,
                )
            )
        return ordered
