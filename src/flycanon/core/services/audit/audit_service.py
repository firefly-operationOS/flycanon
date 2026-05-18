# Copyright 2026 Firefly Software Solutions Inc
"""``AuditService`` -- append-only audit + EDA broadcast.

Every mutation in flycanon should call :meth:`AuditService.record`
exactly once. The service persists the row to ``canon_audit_events``
through :class:`AuditRepository` and -- best-effort -- publishes the
same payload on the configured audit topic.

EDA failures are logged but do not abort the mutation: the durable
record is the audit row in Postgres. Downstream consumers that miss
the live event can replay from the table.
"""

from __future__ import annotations

import logging
from typing import Any

from flycanon.config import CanonSettings
from flycanon.models.entities.audit_event import AuditEventRow
from flycanon.models.repositories.audit_repository import AuditRepository

logger = logging.getLogger(__name__)


class AuditService:
    """Persist + publish audit events."""

    def __init__(
        self,
        *,
        repository: AuditRepository,
        event_publisher: object | None,
        settings: CanonSettings,
    ) -> None:
        self._repository = repository
        self._publisher = event_publisher
        self._settings = settings

    async def record(
        self,
        *,
        event_type: str,
        subject_kind: str,
        subject_id: str,
        actor: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditEventRow:
        """Append one audit row and broadcast it on ``flycanon.audit``."""
        row = AuditEventRow(
            event_type=event_type,
            subject_kind=subject_kind,
            subject_id=subject_id,
            actor=actor,
            correlation_id=correlation_id,
            payload_json=dict(payload or {}),
        )
        stored = await self._repository.append(row)
        await self._publish(stored)
        return stored

    async def _publish(self, row: AuditEventRow) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.audit_topic,
                event_type=self._settings.audit_event,
                payload={
                    "id": row.id,
                    "event_type": row.event_type,
                    "subject_kind": row.subject_kind,
                    "subject_id": row.subject_id,
                    "actor": row.actor,
                    "correlation_id": row.correlation_id,
                    "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
                    "payload": dict(row.payload_json or {}),
                },
            )
        except Exception as exc:
            # Log + swallow: the durable trail is in Postgres. The
            # EDA bus is best-effort and consumers can replay from the
            # table when their projection is behind.
            logger.warning(
                "audit publish failed event_type=%s subject_id=%s: %s",
                row.event_type,
                row.subject_id,
                exc,
            )
