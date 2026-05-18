# Copyright 2026 Firefly Software Solutions Inc
"""Cost-tracking ingest + aggregation.

Two responsibilities:

* :meth:`record` -- append one ``canon_cost_events`` row. Called
  from anywhere a FireflyAgent call completes; the agent
  middleware emits an ``agent.completed`` observability event
  carrying the breadcrumbs we need.

* :meth:`aggregate` -- billing rollups. The ``/api/v1/billing``
  endpoint passes the caller's filter / group-by selection.

The service is intentionally agnostic of WHERE the cost data
came from -- a future pluggable middleware can fan-in costs from
external providers (Cohere rerank, Voyage rerank, ...) without
changing the contract.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from pyfly.container import service

from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.repositories.cost_repository import CostRepository

logger = logging.getLogger(__name__)


@service
class CostService:
    def __init__(self, repository: CostRepository) -> None:
        self._repository = repository

    async def record(
        self,
        *,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | Decimal,
        latency_ms: int | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
        subject_kind: str | None = None,
        subject_id: str | None = None,
    ) -> CostEventRow:
        row = CostEventRow(
            agent_name=agent_name,
            model=model,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            total_tokens=int(input_tokens or 0) + int(output_tokens or 0),
            cost_usd=Decimal(str(cost_usd or 0)),
            latency_ms=latency_ms,
            actor=actor,
            correlation_id=correlation_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
        )
        return await self._repository.add(row)

    async def aggregate(
        self,
        *,
        group_by: list[str],
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return await self._repository.aggregate(
            group_by=group_by,
            actor=actor,
            since=since,
            until=until,
        )
