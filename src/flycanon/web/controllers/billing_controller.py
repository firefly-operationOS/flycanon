# Copyright 2026 Firefly Software Solutions Inc
"""Billing endpoint -- ``GET /api/v1/billing``."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from pyfly.container import rest_controller
from pyfly.web import QueryParam, get_mapping, request_mapping

from flycanon.core.services.billing import CostService
from flycanon.interfaces.dtos.billing import BillingReport, BillingRow

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/billing")
class BillingController:
    def __init__(self, cost_service: CostService) -> None:
        self._cost = cost_service

    @get_mapping("")
    async def report(
        self,
        group_by: QueryParam[str] = "date,model",
        actor: QueryParam[str] = "",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
    ) -> BillingReport:
        """Aggregated cost report.

        Query params:

        * ``group_by`` -- comma-separated columns. Allowed:
          ``date`` / ``model`` / ``agent_name`` / ``actor``.
        * ``actor`` -- narrow to a specific actor (a v1 proxy for
          tenant before multi-tenancy lands).
        * ``since`` / ``until`` -- ISO-8601 timestamps to bound
          the window.

        Response: :class:`BillingReport` with per-group rows plus
        the overall ``total_cost_usd`` + ``total_calls`` for the
        same filter window.
        """
        groups = [g.strip() for g in (group_by or "").split(",") if g.strip()]
        since_dt = _parse_iso(since)
        until_dt = _parse_iso(until)
        records = await self._cost.aggregate(
            group_by=groups or ["date"],
            actor=actor or None,
            since=since_dt,
            until=until_dt,
        )

        rows: list[BillingRow] = []
        total_cost = Decimal("0")
        total_calls = 0
        for record in records:
            group = {k: record[k] for k in groups if k in record}
            cost = Decimal(str(record["cost_usd"] or 0))
            total_cost += cost
            total_calls += int(record["calls"] or 0)
            rows.append(
                BillingRow(
                    input_tokens=int(record["input_tokens"] or 0),
                    output_tokens=int(record["output_tokens"] or 0),
                    total_tokens=int(record["total_tokens"] or 0),
                    cost_usd=str(cost),
                    calls=int(record["calls"] or 0),
                    group=group,
                )
            )
        return BillingReport(
            rows=rows,
            total_cost_usd=str(total_cost),
            total_calls=total_calls,
        )


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
