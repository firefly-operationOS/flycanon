# Copyright 2026 Firefly Software Solutions Inc
"""Billing DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BillingRow(BaseModel):
    """One row of the aggregated billing report.

    Group columns echo the request's ``group_by`` selection
    (``date`` / ``model`` / ``agent_name`` / ``actor``). The
    summed columns are always present.
    """

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: str = Field(description="Decimal as string (USD).")
    calls: int = Field(ge=0)
    group: dict[str, Any] = Field(
        default_factory=dict,
        description="Group-column values keyed by name.",
    )


class BillingReport(BaseModel):
    rows: list[BillingRow] = Field(default_factory=list)
    total_cost_usd: str = Field(default="0")
    total_calls: int = Field(default=0, ge=0)


class CostEvent(BaseModel):
    """One ``canon_cost_events`` row, surfaced as a public DTO.

    Mirrors the persistence schema; ``cost_usd`` is serialised as a
    string so JSON consumers don't lose precision the way IEEE-754
    floats would on six-decimal monetary values.
    """

    id: int
    agent_name: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: str
    latency_ms: int | None = None
    actor: str | None = None
    correlation_id: str | None = None
    subject_kind: str | None = None
    subject_id: str | None = None
    occurred_at: datetime


class CostEventsPage(BaseModel):
    """Page of cost events (drill-down view of the aggregated report)."""

    rows: list[CostEvent] = Field(default_factory=list)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class BillingWindow(BaseModel):
    """One window inside :class:`BillingSummary` (24h / 7d / 30d)."""

    since: datetime
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: str
    top_model: str | None = None
    top_model_cost_usd: str = "0"
    top_actor: str | None = None
    top_actor_cost_usd: str = "0"


class BillingSummary(BaseModel):
    """Rolling-window cost snapshot.

    Returned by ``GET /api/v1/billing/summary``. Three windows are
    populated unconditionally so a status banner can display them
    side by side; missing data turns into a zero row, not a 404.
    """

    generated_at: datetime
    last_24h: BillingWindow
    last_7d: BillingWindow
    last_30d: BillingWindow


class TopConsumerRow(BaseModel):
    """One entry in the top-N list keyed by the requested dimension."""

    dimension: str = Field(description="Column we grouped by (model / agent_name / actor).")
    value: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: str
    calls: int = Field(ge=0)


class TopConsumersReport(BaseModel):
    dimension: str
    rows: list[TopConsumerRow] = Field(default_factory=list)


class SubjectCostRow(BaseModel):
    """Cost attribution per ``(subject_kind, subject_id)``."""

    subject_kind: str | None = None
    subject_id: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: str
    calls: int = Field(ge=0)


class SubjectCostReport(BaseModel):
    rows: list[SubjectCostRow] = Field(default_factory=list)


class LatencyRow(BaseModel):
    """Latency percentiles per group bucket."""

    group: dict[str, str] = Field(
        default_factory=dict,
        description="Group-column values keyed by name.",
    )
    count: int = Field(ge=0)
    avg_ms: int = Field(ge=0)
    p50_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    p99_ms: int = Field(ge=0)
    max_ms: int = Field(ge=0)


class LatencyReport(BaseModel):
    rows: list[LatencyRow] = Field(default_factory=list)
