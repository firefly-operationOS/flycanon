# Copyright 2026 Firefly Software Solutions Inc
"""CQRS handlers for the query surface."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.services.query.answer_service import AnswerService
from flycanon.core.services.query.search_service import SearchService
from flycanon.interfaces.dtos.query import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)


@dataclass(frozen=True)
class SearchKnowledgeQuery(Query[SearchResponse]):
    request: SearchRequest


@query_handler
@service
class SearchKnowledgeHandler(QueryHandler[SearchKnowledgeQuery, SearchResponse]):
    def __init__(self, search: SearchService) -> None:
        super().__init__()
        self._search = search

    async def do_handle(self, query: SearchKnowledgeQuery) -> SearchResponse:
        return await self._search.search(query.request)


@dataclass(frozen=True)
class AnswerKnowledgeQuery(Query[AnswerResponse]):
    request: AnswerRequest


@query_handler
@service
class AnswerKnowledgeHandler(QueryHandler[AnswerKnowledgeQuery, AnswerResponse]):
    def __init__(self, answer: AnswerService) -> None:
        super().__init__()
        self._answer = answer

    async def do_handle(self, query: AnswerKnowledgeQuery) -> AnswerResponse:
        return await self._answer.answer(query.request)


__all__ = [
    "AnswerKnowledgeHandler",
    "AnswerKnowledgeQuery",
    "SearchKnowledgeHandler",
    "SearchKnowledgeQuery",
]
