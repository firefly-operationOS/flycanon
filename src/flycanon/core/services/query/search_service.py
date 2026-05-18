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
    return Hit(
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        knowledge_item_id=hit.knowledge_item_id,
        knowledge_version=hit.knowledge_version,
        content=hit.content,
        score=hit.score,
        bm25_rank=hit.bm25_rank,
        vector_rank=hit.vector_rank,
        metadata=dict(hit.metadata or {}),
    )
