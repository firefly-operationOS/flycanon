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

"""CQRS handler for the audit read surface."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.mappers import to_audit_event
from flycanon.interfaces.dtos.audit import AuditPage
from flycanon.models.repositories import AuditRepository


@dataclass(frozen=True)
class ListAuditQuery(Query[AuditPage]):
    subject_id: str | None = None
    subject_kind: str | None = None
    event_type: str | None = None
    limit: int = 50
    offset: int = 0
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class ListAuditHandler(QueryHandler[ListAuditQuery, AuditPage]):
    def __init__(self, repository: AuditRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListAuditQuery) -> AuditPage:
        rows, total = await self._repository.list_events(
            subject_id=query.subject_id,
            subject_kind=query.subject_kind,
            event_type=query.event_type,
            limit=query.limit,
            offset=query.offset,
            tenant_id=query.tenant_id,
            workspace_id=query.workspace_id,
        )
        return AuditPage(
            items=[to_audit_event(r) for r in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )


__all__ = ["ListAuditHandler", "ListAuditQuery"]
