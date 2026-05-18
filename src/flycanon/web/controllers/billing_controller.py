# Copyright 2026 Firefly Software Solutions Inc
"""Billing endpoints under ``/api/v1/billing``.

Six endpoints, all read-only:

* ``GET /api/v1/billing``            -- aggregated report (date / model / agent_name / actor).
* ``GET /api/v1/billing/events``     -- per-call drill-down (incident forensics).
* ``GET /api/v1/billing/summary``    -- rolling-window snapshot (24h / 7d / 30d).
* ``GET /api/v1/billing/top``        -- top-N consumers by cost on a chosen dimension.
* ``GET /api/v1/billing/by-subject`` -- cost attribution per ``(subject_kind, subject_id)``.
* ``GET /api/v1/billing/latency``    -- p50/p95/p99 latency from the same cost-event stream.

Together these answer the four ops questions billing teams actually
ask: "what did we spend?", "who spent it?", "where did it go?", and
"how slow was it while we were spending it?".
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from pyfly.container import rest_controller
from pyfly.kernel import BadRequestException
from pyfly.web import QueryParam, get_mapping, request_mapping

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

logger = logging.getLogger(__name__)


_TOP_DIMENSIONS: frozenset[str] = frozenset({"model", "agent_name", "actor"})
_GROUP_COLUMNS: frozenset[str] = frozenset(
    {"date", "model", "agent_name", "actor"}
)


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

    @get_mapping("/events")
    async def list_events(
        self,
        actor: QueryParam[str] = "",
        agent_name: QueryParam[str] = "",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> CostEventsPage:
        """Per-call drill-down view.

        The aggregator hides the call-level breadcrumbs (correlation
        id, subject id, latency). This endpoint surfaces them so an
        on-call engineer triaging "why did our spend spike at 14:32?"
        can pivot from the aggregate row into the underlying call
        list.
        """
        rows = await self._cost.list_events(
            actor=actor or None,
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
    async def summary(self, actor: QueryParam[str] = "") -> BillingSummary:
        """Rolling-window cost snapshot.

        Three windows -- ``last_24h``, ``last_7d``, ``last_30d`` --
        are always populated. The top model + top actor inside each
        window land in the same envelope so a dashboard can render a
        single banner without a follow-up query.
        """
        payload = await self._cost.summary(actor=actor or None)
        return BillingSummary(
            generated_at=_parse_iso_required(payload["generated_at"]),
            last_24h=_window(payload["last_24h"]),
            last_7d=_window(payload["last_7d"]),
            last_30d=_window(payload["last_30d"]),
        )

    @get_mapping("/top")
    async def top(
        self,
        dimension: QueryParam[str] = "model",
        since: QueryParam[str] = "",
        until: QueryParam[str] = "",
        limit: QueryParam[int] = 10,
    ) -> TopConsumersReport:
        """Top-N consumers by cost on a single dimension.

        ``dimension`` is one of ``model`` / ``agent_name`` / ``actor``.
        The handler returns the highest-cost ``limit`` rows for the
        provided window.
        """
        if dimension not in _TOP_DIMENSIONS:
            raise BadRequestException(
                f"unknown billing top dimension {dimension!r}; "
                f"expected one of: {sorted(_TOP_DIMENSIONS)}"
            )
        rows = await self._cost.top(
            dimension=dimension,
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
        rows = await self._cost.by_subject(
            subject_kind=subject_kind or None,
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
        groups = [g.strip() for g in (group_by or "").split(",") if g.strip()]
        invalid = [g for g in groups if g not in _TOP_DIMENSIONS]
        if invalid:
            raise BadRequestException(
                f"unknown latency group_by columns: {invalid}; "
                f"expected one or more of: {sorted(_TOP_DIMENSIONS)}"
            )
        rows = await self._cost.latency(
            group_by=groups or ["model"],
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
        top_actor=payload.get("top_actor"),
        top_actor_cost_usd=str(payload.get("top_actor_cost_usd") or "0"),
    )
