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

"""``ListSourcesHandler`` -- paginated source list with filters."""

from __future__ import annotations

from dataclasses import dataclass, field

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.mappers import to_source_record
from flycanon.interfaces.dtos.source import SourcesPage
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.repositories import SourceRepository


@dataclass(frozen=True)
class ListSourcesQuery(Query[SourcesPage]):
    statuses: list[SourceStatus] = field(default_factory=list)
    kinds: list[SourceKind] = field(default_factory=list)
    limit: int = 50
    offset: int = 0
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class ListSourcesHandler(QueryHandler[ListSourcesQuery, SourcesPage]):
    def __init__(self, repository: SourceRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListSourcesQuery) -> SourcesPage:
        rows, total = await self._repository.list_sources(
            statuses=[s.value for s in query.statuses] or None,
            kinds=[k.value for k in query.kinds] or None,
            limit=query.limit,
            offset=query.offset,
            tenant_id=query.tenant_id,
            workspace_id=query.workspace_id,
        )
        return SourcesPage(
            items=[to_source_record(r) for r in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )
