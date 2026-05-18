# Copyright 2026 Firefly Software Solutions Inc
"""Audit DTOs.

The audit log is append-only and mirrors every mutation in the
service. Compliance projections subscribe to ``flycanon.audit`` or
query ``GET /api/v1/audit`` for a paginated view.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Single audit-log row."""

    id: str
    occurred_at: datetime
    event_type: str = Field(
        description="Stable, snake-case identifier of the mutation.",
        examples=["source.ingested", "knowledge.published", "candidate.accepted"],
    )
    actor: str | None = Field(
        default=None,
        description="Stable identifier of the human or service that performed the action.",
    )
    subject_id: str = Field(description="Primary entity touched by this event.")
    subject_kind: str = Field(
        description="Type of subject: ``source`` | ``knowledge`` | ``candidate`` | ``taxonomy``.",
    )
    correlation_id: str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    items: list[AuditEvent]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
