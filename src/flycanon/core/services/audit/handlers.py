# Copyright 2026 Firefly Software Solutions Inc
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
        )
        return AuditPage(
            items=[to_audit_event(r) for r in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )


__all__ = ["ListAuditHandler", "ListAuditQuery"]
