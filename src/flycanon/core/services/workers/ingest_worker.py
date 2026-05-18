# Copyright 2026 Firefly Software Solutions Inc
"""``IngestWorker`` -- subscribes to the lifecycle topics, runs forever.

The worker is the durable consumer for events flycanon publishes:
``flycanon.ingest``, ``flycanon.knowledge``, ``flycanon.audit``.
Subscriptions are wired through ``pyfly.eda.EventPublisher.subscribe``;
the broker selection (memory / postgres / redis / kafka) is driven by
``pyfly.yaml``.

For the bootstrap shipped here every handler is observation-only:
the worker structures the event into a log line and records latency
metrics. The hook is in place so reprocessing / DLQ replay logic can
land in follow-ups without touching the lifecycle plumbing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from flycanon.config import CanonSettings
from flycanon.core.services.ingestion import IngestionService  # noqa: F401  (used by the CLI helper)
from flycanon.models.repositories import SourceRepository

logger = logging.getLogger(__name__)


class IngestWorker:
    """Long-running EDA subscriber.

    ``run_forever()`` registers the handlers, kicks off the publisher's
    own consumer loop, and blocks until the process receives a
    cancellation signal.
    """

    def __init__(
        self,
        *,
        ingestion: IngestionService,
        repository: SourceRepository,
        event_publisher: object,
        settings: CanonSettings,
    ) -> None:
        self._ingestion = ingestion
        self._repository = repository
        self._publisher = event_publisher
        self._settings = settings
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        """Subscribe to every lifecycle topic and block until cancelled."""
        # ``subscribe`` is the framework's pattern hook; the pattern is
        # the event_type (or a glob). We register one handler per
        # logical family and let the framework's bus deliver them.
        subscribe = getattr(self._publisher, "subscribe", None)
        if subscribe is not None:
            subscribe("Source*", self._on_source_event)
            subscribe("Knowledge*", self._on_knowledge_event)
            subscribe("Candidate*", self._on_candidate_event)
            subscribe(self._settings.audit_event, self._on_audit_event)

        start = getattr(self._publisher, "start", None)
        if start is not None:
            await start()

        logger.info(
            "ingest worker started topics=%s,%s,%s adapter=%s",
            self._settings.ingest_topic,
            self._settings.knowledge_topic,
            self._settings.audit_topic,
            self._settings.eda_adapter,
        )

        try:
            await self._stop_event.wait()
        finally:
            stop = getattr(self._publisher, "stop", None)
            if stop is not None:
                await stop()
            logger.info("ingest worker stopped")

    def stop(self) -> None:
        """Signal the worker loop to exit (called by CLI shutdown hooks)."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _on_source_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "source.event type=%s source_id=%s n_chunks=%s",
            self._event_type_of(envelope),
            payload.get("source_id"),
            payload.get("n_chunks"),
        )

    async def _on_knowledge_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "knowledge.event type=%s item_id=%s version=%s",
            self._event_type_of(envelope),
            payload.get("item_id"),
            payload.get("version"),
        )

    async def _on_candidate_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "candidate.event type=%s candidate_id=%s",
            self._event_type_of(envelope),
            payload.get("candidate_id"),
        )

    async def _on_audit_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "audit.event type=%s subject_kind=%s subject_id=%s actor=%s",
            payload.get("event_type"),
            payload.get("subject_kind"),
            payload.get("subject_id"),
            payload.get("actor"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_of(envelope: Any) -> dict[str, Any]:
        payload = getattr(envelope, "payload", None)
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _event_type_of(envelope: Any) -> str:
        return str(getattr(envelope, "event_type", "") or "")
