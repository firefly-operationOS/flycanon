# Copyright 2026 Firefly Software Solutions Inc
"""``SearchService`` -- DTO-shaped wrapper over hybrid retrieval.

Translates the public :class:`SearchRequest` into a
:class:`RetrievalFilters` object, runs the retrieval, and hands back
a :class:`SearchResponse` ready for the controller. Keeps the
controller path free of internal types.
"""

from __future__ import annotations

from flycanon.core.services.retrieval.retrieval_service import RetrievalFilters, RetrievalService
from flycanon.interfaces.dtos.query import Hit, SearchRequest, SearchResponse


class SearchService:
    def __init__(self, *, retrieval: RetrievalService) -> None:
        self._retrieval = retrieval

    async def search(self, request: SearchRequest) -> SearchResponse:
        filters = _filters_from_request(request)
        result = await self._retrieval.search(
            query=request.query,
            top_k=request.top_k,
            per_query_k=request.per_query_k,
            filters=filters,
        )
        return SearchResponse(
            hits=[_hit_dto(hit) for hit in result.hits],
            elapsed_ms=result.elapsed_ms,
        )


def _filters_from_request(request: SearchRequest) -> RetrievalFilters:
    return RetrievalFilters(
        source_ids=request.source_ids,
        knowledge_item_ids=request.knowledge_item_ids,
        domains=[d.value for d in (request.domains or [])] or None,
        jurisdictions=[j.value for j in (request.jurisdictions or [])] or None,
        tags=request.tags,
        statuses=[s.value for s in (request.statuses or [])] or None,
    )


def _hit_dto(hit) -> Hit:
    """Project a retrieval ``Hit`` into the public DTO.

    The structured source-side fields (``source_filename``,
    ``source_title``, ``source_kind``, ``source_uri``,
    ``section_path``, ``page``) are promoted from the
    ``hit.metadata`` dict that the retrieval pipeline hydrates so
    callers don't need a second ``GET /api/v1/sources/{id}`` for
    rendering citation labels. Anything else the loader emitted
    stays in ``metadata`` for forward-compat.
    """
    metadata = dict(hit.metadata or {})
    page_raw = metadata.pop("page", None)
    try:
        page = int(page_raw) if page_raw not in (None, "") else None
    except (TypeError, ValueError):
        page = None
    return Hit(
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        source_filename=metadata.pop("source_filename", None) or None,
        source_title=metadata.pop("source_title", None) or None,
        source_kind=metadata.pop("source_kind", None) or None,
        source_uri=metadata.pop("source_uri", None) or None,
        section_path=metadata.pop("section_path", None) or None,
        page=page,
        knowledge_item_id=hit.knowledge_item_id,
        knowledge_version=hit.knowledge_version,
        content=hit.content,
        score=hit.score,
        bm25_rank=hit.bm25_rank,
        vector_rank=hit.vector_rank,
        metadata=metadata,
    )
