# Copyright 2026 Firefly Software Solutions Inc
"""Billing DTOs."""

from __future__ import annotations

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
