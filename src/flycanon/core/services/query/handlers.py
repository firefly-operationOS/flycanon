# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CQRS handlers for the query surface."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.services.query.answer_dispatcher import AnswerDispatcher
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
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class SearchKnowledgeHandler(QueryHandler[SearchKnowledgeQuery, SearchResponse]):
    def __init__(self, search: SearchService) -> None:
        super().__init__()
        self._search = search

    async def do_handle(self, query: SearchKnowledgeQuery) -> SearchResponse:
        return await self._search.search(
            query.request,
            tenant_id=query.tenant_id,
            workspace_id=query.workspace_id,
        )


@dataclass(frozen=True)
class AnswerKnowledgeQuery(Query[AnswerResponse]):
    request: AnswerRequest
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class AnswerKnowledgeHandler(QueryHandler[AnswerKnowledgeQuery, AnswerResponse]):
    def __init__(self, answer: AnswerDispatcher) -> None:
        super().__init__()
        self._answer = answer

    async def do_handle(self, query: AnswerKnowledgeQuery) -> AnswerResponse:
        return await self._answer.answer(
            query.request,
            tenant_id=query.tenant_id,
            workspace_id=query.workspace_id,
        )


__all__ = [
    "AnswerKnowledgeHandler",
    "AnswerKnowledgeQuery",
    "SearchKnowledgeHandler",
    "SearchKnowledgeQuery",
]
