# Copyright 2026 Firefly Software Solutions Inc
"""Audit-log read endpoint under ``/api/v1/audit``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import QueryParam, get_mapping, request_mapping

from flycanon.core.services.audit.handlers import ListAuditQuery
from flycanon.interfaces.dtos.audit import AuditPage


@rest_controller
@request_mapping("/api/v1/audit")
class AuditController:
    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("", tags=["Audit"])
    async def list_audit(
        self,
        subject_id: QueryParam[str] = "",
        subject_kind: QueryParam[str] = "",
        event_type: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> AuditPage:
        """Paginated audit-log view -- filters compose with AND."""
        return await self._queries.query(
            ListAuditQuery(
                subject_id=subject_id or None,
                subject_kind=subject_kind or None,
                event_type=event_type or None,
                limit=limit,
                offset=offset,
            )
        )
