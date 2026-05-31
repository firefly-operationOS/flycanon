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

"""``GetSourceHandler`` -- read a single source row by id."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.mappers import to_source_record
from flycanon.interfaces.dtos.source import SourceRecord
from flycanon.models.repositories import SourceRepository


@dataclass(frozen=True)
class GetSourceQuery(Query[SourceRecord | None]):
    source_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetSourceHandler(QueryHandler[GetSourceQuery, SourceRecord | None]):
    def __init__(self, repository: SourceRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetSourceQuery) -> SourceRecord | None:
        row = await self._repository.get(
            query.source_id,
            tenant_id=query.tenant_id or "default",
            workspace_id=query.workspace_id or "default",
        )
        if row is None:
            return None
        return to_source_record(row)
