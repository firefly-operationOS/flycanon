# Copyright 2026 Firefly Software Solutions Inc
"""Billing DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BillingRow(BaseModel):
    """One row of the aggregated billing report.

    Group columns echo the request's ``group_by`` selection
    (``date`` / ``model`` / ``agent_name``). The summed columns
    are always present. Scope (tenant + workspace) is fixed via
    request headers and never appears in ``group_by``.
    """

    input_tokens: int = Field(ge=0, description="Sum of input tokens across calls in this bucket.")
    output_tokens: int = Field(ge=0, description="Sum of output tokens across calls in this bucket.")
    total_tokens: int = Field(ge=0, description="``input_tokens + output_tokens`` (denormalised).")
    cost_usd: str = Field(description="Total cost in USD, serialised as a decimal string for precision.")
    calls: int = Field(ge=0, description="Number of ``canon_cost_events`` rows that landed in this bucket.")
    group: dict[str, Any] = Field(
        default_factory=dict,
        description="Group-column values keyed by name (matches the request's ``group_by`` selection).",
    )


class BillingReport(BaseModel):
    """``GET /api/v1/billing`` envelope -- aggregated rows + totals."""

    rows: list[BillingRow] = Field(
        default_factory=list,
        description="One row per (group_by combination); the order matches the SQL aggregate.",
    )
    total_cost_usd: str = Field(
        default="0",
        description="Sum of ``rows[*].cost_usd`` for the same filter window.",
    )
    total_calls: int = Field(
        default=0,
        ge=0,
        description="Sum of ``rows[*].calls`` for the same filter window.",
    )


class CostEvent(BaseModel):
    """One ``canon_cost_events`` row, surfaced as a public DTO.

    Mirrors the persistence schema; ``cost_usd`` is serialised as a
    string so JSON consumers don't lose precision the way IEEE-754
    floats would on six-decimal monetary values.
    """

    id: int = Field(description="Monotonic row id (also serves as the pagination cursor).")
    agent_name: str = Field(
        description="Which agent ran (e.g. ``flycanon-answerer``, ``flycanon-conflict-judge``)."
    )
    model: str = Field(description="Model identifier (e.g. ``anthropic:claude-sonnet-4-6``).")
    input_tokens: int = Field(ge=0, description="Input tokens captured from the agent's usage block.")
    output_tokens: int = Field(ge=0, description="Output tokens captured from the agent's usage block.")
    total_tokens: int = Field(ge=0, description="``input_tokens + output_tokens``.")
    cost_usd: str = Field(description="Cost in USD as a decimal string (6-place precision).")
    latency_ms: int | None = Field(
        default=None,
        description="End-to-end latency of the call. ``null`` only when the middleware couldn't capture it.",
    )
    actor: str | None = Field(
        default=None,
        description=(
            "Caller identity for audit / forensics (JWT subject or "
            "agent-token prefix). NOT a scope key -- the (tenant, "
            "workspace) headers drive scoping."
        ),
    )
    correlation_id: str | None = Field(
        default=None,
        description="W3C correlation id -- pivot back into the audit log with this.",
    )
    subject_kind: str | None = Field(
        default=None,
        description=(
            "Optional breadcrumb keying the spend to a domain entity "
            "(``source`` / ``knowledge_item`` / ``conversation`` / ...). "
            "Powers ``/api/v1/billing/by-subject``."
        ),
    )
    subject_id: str | None = Field(default=None, description="Id of the subject the call attributed to.")
    occurred_at: datetime = Field(description="UTC timestamp of the call completion.")


class CostEventsPage(BaseModel):
    """Page of cost events (drill-down view of the aggregated report)."""

    rows: list[CostEvent] = Field(
        default_factory=list,
        description="One row per call, sorted by ``occurred_at`` descending.",
    )
    limit: int = Field(ge=1, description="Page size requested by the caller (1-500).")
    offset: int = Field(ge=0, description="0-based offset of the first row in the page.")


class BillingWindow(BaseModel):
    """One window inside :class:`BillingSummary` (24h / 7d / 30d)."""

    since: datetime = Field(
        description="Lower bound (UTC) of the window. ``until`` is implicitly ``generated_at``."
    )
    calls: int = Field(ge=0, description="Number of ``canon_cost_events`` rows in the window.")
    input_tokens: int = Field(ge=0, description="Sum of input tokens across the window.")
    output_tokens: int = Field(ge=0, description="Sum of output tokens across the window.")
    total_tokens: int = Field(ge=0, description="``input_tokens + output_tokens``.")
    cost_usd: str = Field(description="Window total in USD (decimal string).")
    top_model: str | None = Field(
        default=None,
        description="Most expensive model identifier in this window (``null`` if no rows).",
    )
    top_model_cost_usd: str = Field(default="0", description="USD spend on ``top_model`` in this window.")


class BillingSummary(BaseModel):
    """Rolling-window cost snapshot.

    Returned by ``GET /api/v1/billing/summary``. Three windows are
    populated unconditionally so a status banner can display them
    side by side; missing data turns into a zero row, not a 404.
    """

    generated_at: datetime = Field(description="UTC timestamp the snapshot was computed.")
    last_24h: BillingWindow = Field(description="Trailing 24-hour window.")
    last_7d: BillingWindow = Field(description="Trailing 7-day window.")
    last_30d: BillingWindow = Field(description="Trailing 30-day window.")


class TopConsumerRow(BaseModel):
    """One entry in the top-N list keyed by the requested dimension."""

    dimension: str = Field(description="Column we grouped by (one of ``model`` / ``agent_name``).")
    value: str | None = Field(
        default=None,
        description="The grouped value for this row (``null`` if the row had no value for ``dimension``).",
    )
    input_tokens: int = Field(ge=0, description="Sum of input tokens for this consumer.")
    output_tokens: int = Field(ge=0, description="Sum of output tokens for this consumer.")
    total_tokens: int = Field(ge=0, description="``input_tokens + output_tokens``.")
    cost_usd: str = Field(description="USD spent by this consumer (decimal string).")
    calls: int = Field(ge=0, description="Number of calls attributed to this consumer.")


class TopConsumersReport(BaseModel):
    """``GET /api/v1/billing/top`` envelope -- ranked consumers."""

    dimension: str = Field(description="Which column we ranked on (echoes the request param).")
    rows: list[TopConsumerRow] = Field(
        default_factory=list,
        description="Rows sorted by ``cost_usd`` descending. Length is bounded by the request ``limit``.",
    )


class SubjectCostRow(BaseModel):
    """Cost attribution per ``(subject_kind, subject_id)``."""

    subject_kind: str | None = Field(
        default=None,
        description="Kind of the subject the call attributed to (``source`` / ``knowledge_item`` / ...).",
    )
    subject_id: str | None = Field(default=None, description="Id of the subject.")
    input_tokens: int = Field(ge=0, description="Sum of input tokens attributed to this subject.")
    output_tokens: int = Field(ge=0, description="Sum of output tokens attributed to this subject.")
    total_tokens: int = Field(ge=0, description="``input_tokens + output_tokens``.")
    cost_usd: str = Field(description="USD spent on this subject (decimal string).")
    calls: int = Field(ge=0, description="Number of calls attributed to this subject.")


class SubjectCostReport(BaseModel):
    """``GET /api/v1/billing/by-subject`` envelope."""

    rows: list[SubjectCostRow] = Field(
        default_factory=list,
        description=(
            "One row per ``(subject_kind, subject_id)`` pair. Subjects without a populated pair are excluded."
        ),
    )


class LatencyRow(BaseModel):
    """Latency percentiles per group bucket."""

    group: dict[str, str] = Field(
        default_factory=dict,
        description="Group-column values keyed by name (matches the request's ``group_by`` selection).",
    )
    count: int = Field(ge=0, description="Number of samples in this bucket.")
    avg_ms: int = Field(ge=0, description="Arithmetic mean of ``latency_ms`` across samples.")
    p50_ms: int = Field(ge=0, description="50th percentile (median).")
    p95_ms: int = Field(ge=0, description="95th percentile.")
    p99_ms: int = Field(ge=0, description="99th percentile.")
    max_ms: int = Field(ge=0, description="Worst observed latency in the bucket.")


class LatencyReport(BaseModel):
    """``GET /api/v1/billing/latency`` envelope."""

    rows: list[LatencyRow] = Field(
        default_factory=list,
        description="One row per group bucket. Sorted by sample count descending.",
    )
