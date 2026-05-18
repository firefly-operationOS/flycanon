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
from typing import TYPE_CHECKING

from flycanon.core.services.embeddings import EmbeddingService
from flycanon.core.services.retrieval.corpus_factory import CorpusContext
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.source_repository import SourceRepository

if TYPE_CHECKING:
    from flycanon.core.services.retrieval.query_expander import QueryExpander
    from flycanon.core.services.retrieval.reranker import Reranker

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
        reranker: "Reranker | None" = None,
        reranker_top_n: int = 20,
        query_expander: "QueryExpander | None" = None,
        query_expansion_n: int = 1,
    ) -> None:
        self._context = context
        self._embeddings = embeddings
        self._source_repo = source_repository
        self._chunk_repo = chunk_repository
        self._knowledge_repo = knowledge_repository
        self._default_top_k = default_top_k
        self._default_per_query_k = default_per_query_k
        self._rrf_k = rrf_k
        self._reranker = reranker
        self._reranker_top_n = reranker_top_n
        self._query_expander = query_expander
        self._query_expansion_n = query_expansion_n

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

        # Optional multi-query expansion. When enabled, the retriever
        # walks every paraphrase in one call -- the agentic
        # HybridRetriever already RRFs over the multi-query result
        # lists internally.
        queries = [query]
        if self._query_expander is not None and self._query_expansion_n > 1:
            queries = await self._query_expander.expand(
                query, n=self._query_expansion_n
            )
            if len(queries) > 1:
                logger.info(
                    "query expansion query=%s variants=%d",
                    query[:60],
                    len(queries),
                )

        chunk_hits = await retriever.retrieve(
            queries,
            top_k_per_query=effective_per_query_k,
            top_k_final=effective_top_k * 3,  # widen for post-filter
        )
        # Hydrate with Postgres metadata + apply filters.
        hydrated = await self._hydrate(chunk_hits)
        if filters is not None:
            hydrated = list(self._apply_filters(hydrated, filters))

        # Optional cross-encoder rerank. The reranker reads
        # ``(query, chunk_content)`` pairs and re-orders the
        # candidate list. We feed it the top ``reranker_top_n``
        # post-filter candidates and let it score; then trim to
        # ``effective_top_k``.
        if self._reranker is not None and hydrated:
            ranked = await self._reranker.rerank(
                query=query,
                hits=hydrated[: self._reranker_top_n],
                top_n=effective_top_k,
            )
            hydrated = ranked + hydrated[len(ranked) :]

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
        # One join resolves the chunk -> current knowledge_version
        # linkage so every hit can carry the canonical item_id +
        # version (when the chunk is cited by a live version) without
        # an N+1 lookup. Older / superseded versions are filtered out
        # server-side -- the Hit DTO points at the live row.
        knowledge_links = await self._knowledge_repo.lookup_published_citations_for_chunks(
            list(rows.keys())
        )
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
                # Seed the source's domain / jurisdiction / tags hints
                # under ``source_*`` keys so the retrieval post-filter
                # can fall back to them when there's no live knowledge
                # linkage. SourceMetadata.domain / .jurisdiction / .tags
                # are caller-supplied at intake time -- not normative,
                # but enough to narrow a result set when no knowledge
                # version has been published yet.
                if source_meta.get("domain"):
                    metadata.setdefault("source_domain", str(source_meta["domain"]))
                if source_meta.get("jurisdiction"):
                    metadata.setdefault(
                        "source_jurisdiction", str(source_meta["jurisdiction"])
                    )
                source_tags = source_meta.get("tags") or []
                if source_tags:
                    metadata.setdefault(
                        "source_tags", ",".join(str(t) for t in source_tags)
                    )
            # Chunk-level breadcrumbs.
            if row.section_path:
                metadata.setdefault("section_path", row.section_path)
            if row.page is not None:
                metadata.setdefault("page", str(row.page))
            link = knowledge_links.get(chunk_id)
            knowledge_item_id = link.item_id if link else None
            knowledge_version = link.version if link else None
            # Stash the resolved knowledge dimensions on metadata so the
            # post-retrieval filters can match without a second repo
            # round-trip per hit. Keys are ``_knowledge_*``-prefixed so
            # callers know they're filter-only (not part of the public
            # metadata contract).
            if link is not None:
                metadata.setdefault("_knowledge_status", link.status)
                metadata.setdefault("_knowledge_domain", link.domain)
                metadata.setdefault("_knowledge_jurisdiction", link.jurisdiction)
                if link.tags:
                    metadata.setdefault("_knowledge_tags", ",".join(link.tags))
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    source_id=row.source_id,
                    knowledge_item_id=knowledge_item_id,
                    knowledge_version=knowledge_version,
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
        """Compose AND filters over the hydrated hits.

        Filters land at the knowledge layer (``knowledge_item_ids``,
        ``statuses``, ``domains``, ``jurisdictions``, ``tags``) via the
        chunk -> live knowledge_version linkage resolved during
        hydration. A hit whose chunk is not cited by any current
        knowledge version is matched ONLY by the ``source_ids`` filter;
        the knowledge-layer filters require a linkage to apply.

        ``domains`` / ``jurisdictions`` / ``tags`` are read from the
        live knowledge_version row when present (resolved upstream and
        cached on the hit's ``metadata`` under ``_knowledge_*`` keys
        by :meth:`_resolve_knowledge_dimensions`); when no knowledge
        linkage exists we fall back to the source's ``metadata_json``
        domain / jurisdiction hint so caller filters still narrow the
        result set.
        """
        source_set = set(filters.source_ids) if filters.source_ids else None
        item_set = set(filters.knowledge_item_ids) if filters.knowledge_item_ids else None
        status_set = set(filters.statuses) if filters.statuses else None
        domain_set = set(filters.domains) if filters.domains else None
        juris_set = set(filters.jurisdictions) if filters.jurisdictions else None
        tag_set = set(filters.tags) if filters.tags else None
        for hit in hits:
            if source_set and hit.source_id not in source_set:
                continue
            if item_set and (hit.knowledge_item_id or "") not in item_set:
                continue
            if status_set:
                status = hit.metadata.get("_knowledge_status")
                if status not in status_set:
                    continue
            if domain_set:
                domain = hit.metadata.get("_knowledge_domain") or hit.metadata.get(
                    "source_domain"
                )
                if domain not in domain_set:
                    continue
            if juris_set:
                juris = hit.metadata.get("_knowledge_jurisdiction") or hit.metadata.get(
                    "source_jurisdiction"
                )
                if juris not in juris_set:
                    continue
            if tag_set:
                hit_tags = hit.metadata.get("_knowledge_tags") or hit.metadata.get(
                    "source_tags"
                ) or ""
                # Tags ride as a comma-separated string for the
                # metadata bag's str-typed contract; ``in`` performs
                # substring lookup which is fine for the canonical
                # tag values (kebab-case slugs, no commas).
                hit_tag_values = {t.strip() for t in str(hit_tags).split(",") if t.strip()}
                if not (tag_set & hit_tag_values):
                    continue
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
