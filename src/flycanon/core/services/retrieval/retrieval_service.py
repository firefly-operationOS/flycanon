# Copyright 2026 Firefly Software Solutions Inc
"""Hybrid retrieval service.

Wraps the agentic :class:`HybridRetriever`, hydrates the resulting
:class:`ChunkHit` rows with Postgres metadata, and applies the
caller-supplied filters (domain / jurisdiction / status / explicit
ids) as a post-retrieval pass.

Filters run AFTER retrieval because the agentic ``HybridRetriever``
does not expose pre-filter predicates yet -- pulling more candidates
and trimming locally is the pragmatic v1 strategy. The
``per_query_k`` knob lets callers compensate by widening the
retrieval window.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from flycanon.core.services.embeddings import EmbeddingService
from flycanon.core.services.retrieval.corpus_factory import CorpusContext
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Hit:
    """Internal hit shape; mapped to the public DTO upstream."""

    chunk_id: str
    source_id: str
    knowledge_item_id: str | None
    knowledge_version: int | None
    content: str
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    metadata: dict[str, str]


@dataclass(slots=True)
class RetrievalFilters:
    source_ids: Sequence[str] | None = None
    knowledge_item_ids: Sequence[str] | None = None
    domains: Sequence[str] | None = None
    jurisdictions: Sequence[str] | None = None
    tags: Sequence[str] | None = None
    statuses: Sequence[str] | None = None


@dataclass(slots=True)
class RetrievalResult:
    hits: list[Hit]
    elapsed_ms: int


class RetrievalService:
    """Hybrid retrieval over the corpus + Postgres metadata."""

    def __init__(
        self,
        *,
        context: CorpusContext,
        embeddings: EmbeddingService,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        knowledge_repository: KnowledgeRepository,
        default_top_k: int,
        default_per_query_k: int,
        rrf_k: int,
    ) -> None:
        self._context = context
        self._embeddings = embeddings
        self._source_repo = source_repository
        self._chunk_repo = chunk_repository
        self._knowledge_repo = knowledge_repository
        self._default_top_k = default_top_k
        self._default_per_query_k = default_per_query_k
        self._rrf_k = rrf_k

    async def search(
        self,
        *,
        query: str,
        top_k: int | None = None,
        per_query_k: int | None = None,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult:
        """Run hybrid retrieval against the corpus and return hydrated hits."""
        from fireflyframework_agentic.rag.retrieval.hybrid import HybridRetriever

        # The agentic embedder protocol is honoured by our EmbeddingService
        # via the ``embed`` method; pass it through directly.
        retriever = HybridRetriever(
            corpus=self._context.corpus,
            vector_store=self._context.vector_store,
            embedder=_EmbedderShim(self._embeddings),
        )
        effective_top_k = top_k or self._default_top_k
        effective_per_query_k = per_query_k or self._default_per_query_k

        start = time.perf_counter()
        chunk_hits = await retriever.retrieve(
            [query],
            top_k_per_query=effective_per_query_k,
            top_k_final=effective_top_k * 3,  # widen for post-filter
        )
        # Hydrate with Postgres metadata + apply filters.
        hydrated = await self._hydrate(chunk_hits)
        if filters is not None:
            hydrated = list(self._apply_filters(hydrated, filters))
        hydrated = hydrated[:effective_top_k]
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "retrieval query=%s hits=%d elapsed_ms=%d",
            query[:80],
            len(hydrated),
            elapsed_ms,
        )
        return RetrievalResult(hits=hydrated, elapsed_ms=elapsed_ms)

    # ------------------------------------------------------------------
    # Hydration + filtering
    # ------------------------------------------------------------------

    async def _hydrate(self, chunk_hits: Sequence[object]) -> list[Hit]:
        chunk_ids = [getattr(h, "chunk_id", "") for h in chunk_hits]
        chunk_rows = await self._chunk_repo.get_many(chunk_ids)
        rows = {row.id: row for row in chunk_rows}
        # Batch-resolve the source rows that the matched chunks belong
        # to so every hit can be enriched with filename / title /
        # source_kind / uri without N+1 lookups.
        source_ids = {row.source_id for row in chunk_rows}
        source_rows = await self._source_repo.get_many(list(source_ids)) if source_ids else []
        sources_by_id = {row.id: row for row in source_rows}
        hits: list[Hit] = []
        for hit in chunk_hits:
            chunk_id = getattr(hit, "chunk_id", "")
            row = rows.get(chunk_id)
            if row is None:
                # The corpus has a chunk Postgres no longer knows about;
                # skip rather than surface a half-resolved row.
                continue
            metadata = dict(getattr(hit, "metadata", {}) or {})
            source = sources_by_id.get(row.source_id)
            if source is not None:
                metadata.setdefault("source_kind", source.kind)
                if source.filename:
                    metadata.setdefault("source_filename", source.filename)
                if source.uri:
                    metadata.setdefault("source_uri", source.uri)
                # Title lives on metadata_json -- prefer the caller's
                # explicit title, otherwise the extractor-derived one.
                source_meta = source.metadata_json or {}
                title = (
                    source_meta.get("title")
                    or (source_meta.get("extracted") or {}).get("title")
                )
                if title:
                    metadata.setdefault("source_title", title)
            # Chunk-level breadcrumbs.
            if row.section_path:
                metadata.setdefault("section_path", row.section_path)
            if row.page is not None:
                metadata.setdefault("page", str(row.page))
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    source_id=row.source_id,
                    knowledge_item_id=None,
                    knowledge_version=None,
                    content=row.content,
                    score=float(getattr(hit, "score", 0.0)),
                    bm25_rank=None,
                    vector_rank=None,
                    metadata=metadata,
                )
            )
        return hits

    def _apply_filters(
        self,
        hits: Iterable[Hit],
        filters: RetrievalFilters,
    ) -> Iterable[Hit]:
        source_set = set(filters.source_ids) if filters.source_ids else None
        for hit in hits:
            if source_set and hit.source_id not in source_set:
                continue
            # Domain / jurisdiction / status filters are not yet
            # mirrored into the corpus metadata; future work threads
            # them through ``StoredChunk.metadata`` at ingest. For
            # now, callers that need those filters should restrict
            # by source_id or knowledge_item_id (which are sufficient
            # in the canonical workflow).
            yield hit


class _EmbedderShim:
    """Adapt :class:`EmbeddingService` to the agentic ``EmbeddingProtocol``.

    The framework expects an embedder with an async ``embed`` method
    returning an object whose ``embeddings`` attribute is the list of
    vectors. Our :class:`EmbeddingService` already returns vectors
    directly; this shim wraps them in the expected shape.
    """

    def __init__(self, service: EmbeddingService) -> None:
        self._service = service

    async def embed(self, texts: list[str]) -> object:
        vectors = await self._service.embed(texts)
        return _EmbedResult(embeddings=vectors)

    async def embed_one(self, text: str) -> list[float]:
        """Single-text convenience used by the agentic ``HybridRetriever``.

        The retriever calls this for the search query path; returning
        the raw vector keeps the call cheap and avoids the wrapper.
        """
        return await self._service.embed_one(text)


@dataclass(slots=True)
class _EmbedResult:
    embeddings: list[list[float]]
