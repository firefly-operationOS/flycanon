# Copyright 2026 Firefly Software Solutions Inc
"""Hybrid retrieval -- BM25 + dense vectors fused via Reciprocal Rank Fusion.

These types used to live in ``fireflyframework_agentic.rag`` (``StoredChunk``,
``ChunkHit``, ``HybridRetriever``, ``reciprocal_rank_fusion``). The framework's
RAG module has been removed, so flycanon owns them directly. The call contract
the orchestration relies on is preserved exactly -- ``corpus.bm25_search`` /
``corpus.get_chunks`` / ``vector_store.search`` / ``embedder.embed_one`` -- so
the scope-binding wrappers in :mod:`retrieval_service` need no change.

The module is self-contained: it depends only on the stdlib, and types its
collaborators via structural :class:`Protocol`s. The pgvector-default path
(``PostgresCorpus`` + ``PgVectorVectorStore`` + ``EmbeddingService``) satisfies
those protocols structurally.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StoredChunk:
    """A persisted chunk row (BM25 corpus + vector store share this shape)."""

    chunk_id: str
    doc_id: str
    source_path: str
    index_in_doc: int
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChunkHit:
    """A retrieval hit -- a chunk plus its fused relevance score."""

    chunk_id: str
    score: float
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    doc_id: str = ""


class _CorpusLike(Protocol):
    async def bm25_search(self, text: str, *, top_k: int) -> list[ChunkHit]: ...
    async def get_chunks(self, chunk_ids: Sequence[str]) -> list[StoredChunk]: ...


class _VectorStoreLike(Protocol):
    async def search(self, query_embedding: list[float], *, top_k: int) -> list[Any]: ...


class _EmbedderLike(Protocol):
    async def embed_one(self, text: str) -> list[float]: ...


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over multiple ranked lists.

    Given a list of rankings (each a sequence of ``chunk_id`` strings ordered
    best-first), returns ``[(chunk_id, score), ...]`` sorted by descending RRF
    score. Score formula: ``Σ_r 1 / (k + rank_r)`` where ``rank_r`` is the
    1-indexed rank in ranking ``r``. The ``k`` constant (Cormack et al.
    default 60) smooths out lower-ranked items.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class HybridRetriever:
    """Hybrid retrieval: BM25 + dense vectors, fused via RRF.

    For each input query, runs BM25 (unless the query is ``vec_only``) and
    vector search, then fuses every ranking via Reciprocal Rank Fusion.
    Queries may be plain strings (route defaults to ``hybrid``) or any object
    exposing ``.text`` / ``.route`` attributes.

    ``rrf_k`` is honoured (the framework version hard-coded 60), so flycanon's
    ``FLYCANON_RETRIEVAL_RRF_K`` knob is now live.
    """

    def __init__(
        self,
        *,
        corpus: _CorpusLike,
        vector_store: _VectorStoreLike,
        embedder: _EmbedderLike,
        rrf_k: int = 60,
    ) -> None:
        self._corpus = corpus
        self._vector_store = vector_store
        self._embedder = embedder
        self._rrf_k = rrf_k

    async def retrieve(
        self,
        queries: Sequence[Any],
        *,
        top_k_per_query: int = 30,
        top_k_final: int = 10,
    ) -> list[ChunkHit]:
        if not queries:
            return []

        rankings: list[list[str]] = []
        for raw_q in queries:
            text = raw_q.text if hasattr(raw_q, "text") else raw_q
            route = getattr(raw_q, "route", "hybrid")

            # BM25 -- skipped for vec_only (e.g. HyDE) queries.
            if route != "vec_only":
                try:
                    bm25_hits = await self._corpus.bm25_search(text, top_k=top_k_per_query)
                    rankings.append([h.chunk_id for h in bm25_hits])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("bm25 failed for query %r: %s", text, exc)

            # Vector -- always.
            try:
                qvec = await self._embedder.embed_one(text)
                vec_hits = await self._vector_store.search(qvec, top_k=top_k_per_query)
                rankings.append([h.document.id for h in vec_hits])
            except Exception as exc:  # noqa: BLE001
                logger.warning("vector search failed for query %r: %s", text, exc)

        if not any(rankings):
            return []

        fused = reciprocal_rank_fusion(rankings, k=self._rrf_k)
        top_ids = [cid for cid, _ in fused[:top_k_final]]
        if not top_ids:
            return []

        score_by_id = dict(fused[:top_k_final])
        stored = await self._corpus.get_chunks(top_ids)
        stored_by_id = {c.chunk_id: c for c in stored}

        results: list[ChunkHit] = []
        for cid in top_ids:
            chunk = stored_by_id.get(cid)
            if chunk is None:
                continue
            results.append(
                ChunkHit(
                    chunk_id=chunk.chunk_id,
                    score=score_by_id[cid],
                    content=chunk.content,
                    metadata=chunk.metadata,
                    source_path=chunk.source_path,
                    doc_id=chunk.doc_id,
                )
            )
        return results
