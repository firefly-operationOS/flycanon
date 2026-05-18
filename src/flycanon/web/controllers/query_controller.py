# Copyright 2026 Firefly Software Solutions Inc
"""Search + RAG-answer endpoints under ``/api/v1``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import Body, Valid, post_mapping, request_mapping

from flycanon.core.services.query.handlers import (
    AnswerKnowledgeQuery,
    SearchKnowledgeQuery,
)
from flycanon.interfaces.dtos.query import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)


@rest_controller
@request_mapping("/api/v1")
class QueryController:
    """Hybrid search + grounded answering."""

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @post_mapping("/search", tags=["Query"])
    async def search(self, request: Valid[Body[SearchRequest]]) -> SearchResponse:
        """Hybrid retrieval over the corpus -- BM25 + vector with RRF
        fusion. Returns the fused hit list, no LLM call."""
        return await self._queries.query(SearchKnowledgeQuery(request=request))

    @post_mapping("/query", tags=["Query"])
    async def answer(self, request: Valid[Body[AnswerRequest]]) -> AnswerResponse:
        """Grounded RAG answer with citations. Runs hybrid retrieval
        then asks the configured answer model to write an answer using
        only the top hits."""
        return await self._queries.query(AnswerKnowledgeQuery(request=request))
