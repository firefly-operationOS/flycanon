# Copyright 2026 Firefly Software Solutions Inc
"""``AuditEventRow`` -> :class:`AuditEvent` DTO."""

from __future__ import annotations

from flycanon.interfaces.dtos.audit import AuditEvent
from flycanon.models.entities.audit_event import AuditEventRow


def to_audit_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        occurred_at=row.occurred_at,
        event_type=row.event_type,
        actor=row.actor,
        subject_id=row.subject_id,
        subject_kind=row.subject_kind,
        correlation_id=row.correlation_id,
        payload=dict(row.payload_json or {}),
    )
