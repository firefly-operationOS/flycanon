# Copyright 2026 Firefly Software Solutions Inc
"""Cost-tracking ingest + aggregation.

Three responsibilities:

* :meth:`record` -- append one ``canon_cost_events`` row. Called
  from anywhere a FireflyAgent call completes; the agent
  middleware emits an ``agent.completed`` observability event
  carrying the breadcrumbs we need.

* :meth:`aggregate` -- billing rollups. The ``/api/v1/billing``
  endpoint passes the caller's filter / group-by selection.

* Drill-down queries (:meth:`list_events`, :meth:`summary`,
  :meth:`top`, :meth:`by_subject`, :meth:`latency`) -- expose the
  per-call detail captured at record time so the dashboards can
  answer "right-now spend", "top spenders", "cost of indexing source
  X" without a second JOIN against external billing data.

The service is intentionally agnostic of WHERE the cost data
came from -- a future pluggable middleware can fan-in costs from
external providers (Cohere rerank, Voyage rerank, ...) without
changing the contract.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pyfly.container import service

from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.repositories.cost_repository import CostRepository

logger = logging.getLogger(__name__)


_DEFAULT_TOP_LIMIT = 10
_DEFAULT_PAGE_LIMIT = 100
_SUMMARY_WINDOWS = (
    ("last_24h", timedelta(hours=24)),
    ("last_7d", timedelta(days=7)),
    ("last_30d", timedelta(days=30)),
)


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
        tenant_id: str = "default",
        workspace_id: str = "default",
        latency_ms: int | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
        subject_kind: str | None = None,
        subject_id: str | None = None,
    ) -> CostEventRow:
        """Persist one cost event.

        ``tenant_id`` + ``workspace_id`` default to ``"default"`` so the
        ``agent.completed`` observability hook keeps working without
        thread-local context propagation; production callers that
        already have a request scope (the agent-tier wrappers) thread
        the real values through.
        """
        row = CostEventRow(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
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
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        return await self._repository.aggregate(
            group_by=group_by,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            since=since,
            until=until,
        )

    async def list_events(
        self,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        agent_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> list[CostEventRow]:
        """Per-call drill-down. The aggregator hides timing + correlation
        breadcrumbs; this list exposes them for incident forensics."""
        return await self._repository.list_events(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_name=agent_name,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    async def summary(
        self,
        *,
        now: datetime | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot of recent spend across rolling windows.

        Returns a single dict carrying ``last_24h`` / ``last_7d`` /
        ``last_30d`` buckets plus the top model inside each window.
        Designed for a status banner: "$4.20 today, $87 this month,
        top: anthropic:claude-sonnet-4-6".

        Scope is fixed to the (tenant, workspace) passed in -- the
        legacy ``actor`` partitioning proxy was retired in Plan 4.
        """
        now = now or datetime.now(UTC)
        result: dict[str, Any] = {"generated_at": now.isoformat()}
        for label, delta in _SUMMARY_WINDOWS:
            since = now - delta
            rows = await self._repository.aggregate(
                group_by=["model"],
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                since=since,
                until=now,
            )
            total_cost = Decimal("0")
            total_calls = 0
            total_input = 0
            total_output = 0
            top_model: str | None = None
            top_model_cost = Decimal("0")
            for r in rows:
                cost = Decimal(str(r["cost_usd"] or 0))
                total_cost += cost
                total_calls += int(r["calls"] or 0)
                total_input += int(r["input_tokens"] or 0)
                total_output += int(r["output_tokens"] or 0)
                if cost > top_model_cost:
                    top_model_cost = cost
                    top_model = r.get("model")
            result[label] = {
                "since": since.isoformat(),
                "calls": total_calls,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "cost_usd": str(total_cost),
                "top_model": top_model,
                "top_model_cost_usd": str(top_model_cost),
            }
        return result

    async def top(
        self,
        *,
        dimension: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_TOP_LIMIT,
    ) -> list[dict[str, Any]]:
        """Top-N consumers by cost on a single dimension.

        ``dimension`` is one of the columns ``aggregate`` accepts
        (``model`` / ``agent_name``). Returned rows are sorted by
        ``cost_usd`` descending in Python so the same contract works
        on both Postgres and SQLite. ``actor`` is intentionally NOT a
        valid dimension -- it is audit metadata, not a scope key.
        """
        if dimension not in {"model", "agent_name"}:
            raise ValueError(
                f"unknown billing top dimension {dimension!r}; expected one of: model, agent_name"
            )
        rows = await self._repository.aggregate(
            group_by=[dimension],
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            since=since,
            until=until,
        )
        rows.sort(key=lambda r: Decimal(str(r.get("cost_usd") or 0)), reverse=True)
        return rows[: max(1, int(limit))]

    async def by_subject(
        self,
        *,
        subject_kind: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = _DEFAULT_TOP_LIMIT,
    ) -> list[dict[str, Any]]:
        """Cost attribution per ``(subject_kind, subject_id)`` row.

        Useful for "how much did indexing source X cost?" -- requires
        callers of :meth:`record` to be populating ``subject_kind`` /
        ``subject_id`` (e.g., ``source`` / ``source_id`` for the
        ingestion pipeline, ``knowledge_item`` / ``item_id`` for
        consolidation, etc.).
        """
        return await self._repository.list_subject_costs(
            subject_kind=subject_kind,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            since=since,
            until=until,
            limit=limit,
        )

    async def latency(
        self,
        *,
        group_by: list[str],
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Latency percentiles (p50 / p95 / p99) per group.

        Pulled from the per-call ``latency_ms`` column, which is
        captured alongside cost on every FireflyAgent invocation. The
        result is a sorted list with one entry per group bucket.
        """
        groups = [g for g in group_by if g in {"model", "agent_name"}] or ["model"]
        samples = await self._repository.latency_samples(
            group_by=groups,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            since=since,
            until=until,
        )
        out: list[dict[str, Any]] = []
        for key, latencies in samples.items():
            if not latencies:
                continue
            latencies.sort()
            record: dict[str, Any] = dict(zip(groups, key, strict=True))
            record["count"] = len(latencies)
            record["p50_ms"] = int(_percentile(latencies, 0.50))
            record["p95_ms"] = int(_percentile(latencies, 0.95))
            record["p99_ms"] = int(_percentile(latencies, 0.99))
            record["avg_ms"] = int(round(statistics.fmean(latencies)))
            record["max_ms"] = int(latencies[-1])
            out.append(record)
        out.sort(key=lambda r: r["count"], reverse=True)
        return out


def _percentile(sorted_values: list[int], q: float) -> float:
    """Linear-interpolation percentile.

    Equivalent to numpy's default behaviour without the dependency:
    ``q`` is in ``[0, 1]``; for an empty list returns ``0``.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = q * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac
