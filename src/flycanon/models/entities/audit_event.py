# Copyright 2026 Firefly Software Solutions Inc
"""``canon_audit_events`` -- append-only audit log.

Top-level columns capture the canonical lookup keys (event type,
subject, actor, correlation); the free-form ``payload_json`` carries
the rest. The structure matches the
``audit_event_metrics`` memory note: aggregations join on the columns,
not on the JSON.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class AuditEventRow(Base):
    __tablename__ = "canon_audit_events"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    actor: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_canon_audit_subject", "subject_kind", "subject_id"),)
