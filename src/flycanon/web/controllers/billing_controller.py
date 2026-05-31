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

"""Billing endpoints under ``/api/v1/billing``.

Six endpoints, all read-only:

* ``GET /api/v1/billing``            -- aggregated report (date / model / agent_name).
* ``GET /api/v1/billing/events``     -- per-call drill-down (incident forensics).
* ``GET /api/v1/billing/summary``    -- rolling-window snapshot (24h / 7d / 30d).
* ``GET /api/v1/billing/top``        -- top-N consumers by cost on a chosen dimension.
* ``GET /api/v1/billing/by-subject`` -- cost attribution per ``(subject_kind, subject_id)``.
* ``GET /api/v1/billing/latency``    -- p50/p95/p99 latency from the same cost-event stream.

All endpoints are scoped to the (tenant, workspace) carried by the
``X-Tenant-Id`` + ``X-Workspace-Id`` request headers; cross-tenant
and cross-workspace queries are out of scope for v1.

Together these answer the four ops questions billing teams actually
ask: "what did we spend?", "who spent it?", "where did it go?", and
"how slow was it while we were spending it?".
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from pyfly.container import rest_controller
from pyfly.kernel import InvalidRequestException
from pyfly.web import QueryParam, get_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.billing import CostService
from flycanon.interfaces.dtos.billing import (
    BillingReport,
    BillingRow,
    BillingSummary,
    BillingWindow,
    CostEvent,
    CostEventsPage,
    LatencyReport,
    LatencyRow,
    SubjectCostReport,
    SubjectCostRow,
    TopConsumerRow,
    TopConsumersReport,
)
from flycanon.web.conventions import TenantContext, tenant_context_from_request

logger = logging.getLogger(__name__)


# Scope is fixed to (tenant_id, workspace_id) from request headers --
# cross-tenant / cross-workspace billing reports are out of scope for
# the v1 contract. The dimensions below only enumerate columns that
# vary WITHIN one workspace; ``actor`` is audit metadata, never a
# partitioning key.
_TOP_DIMENSIONS: frozenset[str] = frozenset({"model", "agent_name"})
_LATENCY_DIMENSIONS: frozenset[str] = frozenset({"model", "agent_name"})


@rest_controller
@request_mapping("/api/v1/billing")
class BillingController:
    def __init__(self, cost_service: CostService) -> None:
        self._cost = cost_service

    @get_mapping("")
    async def report(
        self,
        http_request: Request,
        group_by: QueryParam[str] = "date,model",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
    ) -> BillingReport:
        """Aggregated cost report.

        Scope is fixed to the (tenant, workspace) carried by the
        request headers; cross-scope rollups are out of v1.

        Query params:

        * ``group_by`` -- comma-separated columns. Allowed:
          ``date`` / ``model`` / ``agent_name``.
        * ``since`` / ``until`` -- ISO-8601 timestamps to bound
          the window.

        Response: :class:`BillingReport` with per-group rows plus
        the overall ``total_cost_usd`` + ``total_calls`` for the
        same filter window.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        groups = [g.strip() for g in (group_by or "").split(",") if g.strip()]
        records = await self._cost.aggregate(
            group_by=groups or ["date"],
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            since=_parse_iso(since),
            until=_parse_iso(until),
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

    @get_mapping("/events")
    async def list_events(
        self,
        http_request: Request,
        agent_name: QueryParam[str] = "",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> CostEventsPage:
        """Per-call drill-down view.

        The aggregator hides the call-level breadcrumbs (correlation
        id, subject id, latency, actor). This endpoint surfaces them
        so an on-call engineer triaging "why did our spend spike at
        14:32?" can pivot from the aggregate row into the underlying
        call list. ``actor`` is retained as audit metadata on each
        row, but is no longer a scope filter.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        rows = await self._cost.list_events(
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            agent_name=agent_name or None,
            since=_parse_iso(since),
            until=_parse_iso(until),
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return CostEventsPage(
            rows=[
                CostEvent(
                    id=int(r.id),
                    agent_name=r.agent_name,
                    model=r.model,
                    input_tokens=int(r.input_tokens or 0),
                    output_tokens=int(r.output_tokens or 0),
                    total_tokens=int(r.total_tokens or 0),
                    cost_usd=str(r.cost_usd or 0),
                    latency_ms=int(r.latency_ms) if r.latency_ms is not None else None,
                    actor=r.actor,
                    correlation_id=r.correlation_id,
                    subject_kind=r.subject_kind,
                    subject_id=r.subject_id,
                    occurred_at=r.occurred_at,
                )
                for r in rows
            ],
            limit=int(limit),
            offset=int(offset),
        )

    @get_mapping("/summary")
    async def summary(self, http_request: Request) -> BillingSummary:
        """Rolling-window cost snapshot.

        Three windows -- ``last_24h``, ``last_7d``, ``last_30d`` --
        are always populated. The top model inside each window lands
        in the same envelope so a dashboard can render a single
        banner without a follow-up query.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        payload = await self._cost.summary(
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
        )
        return BillingSummary(
            generated_at=_parse_iso_required(payload["generated_at"]),
            last_24h=_window(payload["last_24h"]),
            last_7d=_window(payload["last_7d"]),
            last_30d=_window(payload["last_30d"]),
        )

    @get_mapping("/top")
    async def top(
        self,
        http_request: Request,
        dimension: QueryParam[str] = "model",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
        limit: QueryParam[int] = 10,
    ) -> TopConsumersReport:
        """Top-N consumers by cost on a single dimension.

        ``dimension`` is one of ``model`` / ``agent_name``. The
        handler returns the highest-cost ``limit`` rows for the
        provided window, scoped to the request headers.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        if dimension not in _TOP_DIMENSIONS:
            raise InvalidRequestException(
                f"unknown billing top dimension {dimension!r}; expected one of: {sorted(_TOP_DIMENSIONS)}"
            )
        rows = await self._cost.top(
            dimension=dimension,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            since=_parse_iso(since),
            until=_parse_iso(until),
            limit=max(1, min(int(limit), 100)),
        )
        out: list[TopConsumerRow] = []
        for r in rows:
            out.append(
                TopConsumerRow(
                    dimension=dimension,
                    value=str(r[dimension]) if r.get(dimension) is not None else None,
                    input_tokens=int(r["input_tokens"] or 0),
                    output_tokens=int(r["output_tokens"] or 0),
                    total_tokens=int(r["total_tokens"] or 0),
                    cost_usd=str(r["cost_usd"] or 0),
                    calls=int(r["calls"] or 0),
                )
            )
        return TopConsumersReport(dimension=dimension, rows=out)

    @get_mapping("/by-subject")
    async def by_subject(
        self,
        http_request: Request,
        subject_kind: QueryParam[str] = "",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
        limit: QueryParam[int] = 20,
    ) -> SubjectCostReport:
        """Cost attribution per ``(subject_kind, subject_id)``.

        Useful for answering "how much did indexing source X cost?"
        Caller-side instrumentation must populate ``subject_kind`` /
        ``subject_id`` when recording the cost event; rows without a
        subject are excluded from this report.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        rows = await self._cost.by_subject(
            subject_kind=subject_kind or None,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            since=_parse_iso(since),
            until=_parse_iso(until),
            limit=max(1, min(int(limit), 200)),
        )
        return SubjectCostReport(
            rows=[
                SubjectCostRow(
                    subject_kind=r.get("subject_kind"),
                    subject_id=r.get("subject_id"),
                    input_tokens=int(r["input_tokens"] or 0),
                    output_tokens=int(r["output_tokens"] or 0),
                    total_tokens=int(r["total_tokens"] or 0),
                    cost_usd=str(r["cost_usd"] or 0),
                    calls=int(r["calls"] or 0),
                )
                for r in rows
            ]
        )

    @get_mapping("/latency")
    async def latency(
        self,
        http_request: Request,
        group_by: QueryParam[str] = "model",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
    ) -> LatencyReport:
        """Latency percentiles -- p50 / p95 / p99 -- per group bucket.

        Pulled from the same ``canon_cost_events`` stream: each row's
        ``latency_ms`` is captured at record time alongside cost. The
        computation runs in Python so the SQL backend doesn't need
        ``percentile_cont`` (kept dialect-agnostic for the SQLite
        test path).
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        groups = [g.strip() for g in (group_by or "").split(",") if g.strip()]
        invalid = [g for g in groups if g not in _LATENCY_DIMENSIONS]
        if invalid:
            raise InvalidRequestException(
                f"unknown latency group_by columns: {invalid}; "
                f"expected one or more of: {sorted(_LATENCY_DIMENSIONS)}"
            )
        rows = await self._cost.latency(
            group_by=groups or ["model"],
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            since=_parse_iso(since),
            until=_parse_iso(until),
        )
        out: list[LatencyRow] = []
        for r in rows:
            group = {g: str(r[g]) for g in (groups or ["model"]) if g in r}
            out.append(
                LatencyRow(
                    group=group,
                    count=int(r["count"]),
                    avg_ms=int(r["avg_ms"]),
                    p50_ms=int(r["p50_ms"]),
                    p95_ms=int(r["p95_ms"]),
                    p99_ms=int(r["p99_ms"]),
                    max_ms=int(r["max_ms"]),
                )
            )
        return LatencyReport(rows=out)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_required(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _window(payload: dict) -> BillingWindow:
    return BillingWindow(
        since=_parse_iso_required(payload["since"]),
        calls=int(payload["calls"]),
        input_tokens=int(payload["input_tokens"]),
        output_tokens=int(payload["output_tokens"]),
        total_tokens=int(payload["total_tokens"]),
        cost_usd=str(payload["cost_usd"]),
        top_model=payload.get("top_model"),
        top_model_cost_usd=str(payload.get("top_model_cost_usd") or "0"),
    )
