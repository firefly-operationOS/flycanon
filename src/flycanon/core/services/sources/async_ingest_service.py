# Copyright 2026 Firefly Software Solutions Inc
"""Async-ingest orchestrator.

Public surface (controller side): :meth:`submit_async` creates a
``canon_ingest_jobs`` row in ``queued`` status and broadcasts an
``IngestSourceRequested`` event on the ingest topic. The job's
payload (bytes + metadata) lives on the row's ``metadata_json``
column so the worker can pick it up by id without any out-of-band
storage.

Worker surface: :meth:`process` runs the full intake pipeline for
one job. The :class:`IngestWorker` subscribes to the
``IngestSourceRequested`` event and dispatches here.

Lifecycle events are emitted at every stage so the SSE streaming
endpoint can replay them to the caller. The audit log captures
the bookend events (``ingest.queued`` + the terminal outcome).
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pyfly.container import service
from pyfly.eda import EventPublisher

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.interfaces.dtos.source import SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind
from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository

logger = logging.getLogger(__name__)


# Event type the worker subscribes to. Lives on the ``ingest`` topic
# so downstream observers can replay both submit + finish events on
# one subscription if they want.
INGEST_REQUESTED_EVENT = "IngestSourceRequested"


@service
class AsyncIngestService:
    def __init__(
        self,
        intake: IntakeService,
        repository: IngestJobRepository,
        audit: AuditService,
        event_publisher: EventPublisher,
        settings: CanonSettings,
    ) -> None:
        self._intake = intake
        self._repository = repository
        self._audit = audit
        self._publisher = event_publisher
        self._settings = settings

    # ------------------------------------------------------------------
    # Submit side
    # ------------------------------------------------------------------

    async def submit_async(
        self,
        *,
        request: SubmitSourceRequest,
        content: bytes,
        filename: str | None,
        content_type: str | None,
        actor: str | None,
        correlation_id: str | None,
        callback_url: str | None = None,
    ) -> IngestJobRow:
        """Persist a job row + broadcast the IngestSourceRequested event."""
        job_id = str(uuid.uuid4())
        # Bytes ride on the row as base64 inside ``metadata_json``.
        # That keeps the worker decoupled from a separate blob store
        # for v1; production deploys that need 100-MB intakes should
        # land a follow-up that offloads the bytes to S3 + indexes
        # the URL here. For the canonical operational-knowledge
        # corpus (mostly Office docs, PDFs, transcripts) a row-bound
        # payload is fine.
        payload: dict[str, Any] = {
            "content_base64": base64.b64encode(content).decode("ascii"),
            "kind": request.kind.value if request.kind else SourceKind.unknown.value,
            "uri": request.uri,
            "metadata": _serialise_metadata(request),
        }
        row = IngestJobRow(
            id=job_id,
            status="queued",
            filename=filename,
            content_type=content_type,
            uri=request.uri,
            actor=actor,
            correlation_id=correlation_id,
            callback_url=callback_url,
            metadata_json=payload,
        )
        stored = await self._repository.add(row)
        await self._repository.append_event(
            stored.id,
            stage="queued",
            message="job queued -- waiting for worker pickup",
        )
        await self._audit.record(
            event_type="ingest.queued",
            subject_kind="ingest_job",
            subject_id=stored.id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "filename": filename,
                "content_type": content_type,
                "uri": request.uri,
            },
        )
        await self._publish_requested(stored.id, correlation_id)
        logger.info(
            "ingest job queued id=%s filename=%s bytes=%d",
            stored.id,
            filename,
            len(content),
        )
        return stored

    async def _publish_requested(self, job_id: str, correlation_id: str | None) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.ingest_topic,
                event_type=INGEST_REQUESTED_EVENT,
                payload={"job_id": job_id},
                headers={"correlation-id": correlation_id} if correlation_id else None,
            )
        except Exception as exc:
            logger.warning("IngestSourceRequested publish failed job_id=%s: %s", job_id, exc)

    # ------------------------------------------------------------------
    # Worker side
    # ------------------------------------------------------------------

    async def process(self, job_id: str) -> None:
        """Run the intake pipeline for one queued job.

        Idempotent on terminal states -- if the job is already
        succeeded / failed we skip without re-running (a duplicate
        EDA delivery should not double-emit citations).
        """
        job = await self._repository.get(job_id)
        if job is None:
            logger.warning("worker received unknown job_id=%s -- dropping", job_id)
            return
        if job.status in ("succeeded", "failed"):
            logger.info(
                "ingest job %s already in terminal state %s -- skipping",
                job_id,
                job.status,
            )
            return

        running = await self._repository.mark_running(job_id)
        if running is None:
            # Another worker already claimed (or finished) this job --
            # the atomic ``UPDATE ... WHERE status = 'queued'``
            # returns nothing when the row has left ``queued``. The
            # original delivery was lost OR another replica beat us;
            # either way, idempotent skip is the right answer.
            logger.info(
                "ingest job %s could not be claimed (already running or terminal) "
                "-- skipping duplicate delivery",
                job_id,
            )
            return
        job = running

        payload = job.metadata_json or {}
        try:
            content_b64 = payload.get("content_base64") or ""
            content = base64.b64decode(content_b64) if content_b64 else b""
            kind_value = payload.get("kind") or SourceKind.unknown.value
            metadata = payload.get("metadata") or {}
            request = SubmitSourceRequest.model_validate(
                {
                    "kind": kind_value,
                    "uri": payload.get("uri"),
                    "metadata": metadata,
                }
            )

            await self._repository.append_event(
                job_id, stage="normalising", message="binary normalise + load"
            )
            source = await self._intake.submit(
                request=request,
                content=content,
                filename=job.filename,
                content_type=job.content_type,
                actor=job.actor,
                correlation_id=job.correlation_id,
            )
            await self._repository.append_event(
                job_id,
                stage="finished",
                message="intake pipeline complete",
                payload={
                    "source_id": source.id,
                    "n_chunks": source.n_chunks,
                    "content_sha256": source.content_sha256,
                },
            )
            await self._repository.mark_succeeded(
                job_id,
                source_id=source.id,
                content_sha256=source.content_sha256,
            )
            await self._audit.record(
                event_type="ingest.succeeded",
                subject_kind="ingest_job",
                subject_id=job_id,
                actor=job.actor,
                correlation_id=job.correlation_id,
                payload={
                    "source_id": source.id,
                    "n_chunks": source.n_chunks,
                },
            )
            await self._publish_finish(
                event_type="IngestSourceFinished",
                job_id=job_id,
                payload={"source_id": source.id, "n_chunks": source.n_chunks},
            )
            logger.info("ingest job succeeded id=%s source_id=%s", job_id, source.id)

            if job.callback_url:
                # Webhook delivery is best-effort -- the durable
                # record is the audit row.
                await self._fire_webhook(job, status="succeeded", source_id=source.id)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None) or type(exc).__name__
            await self._repository.append_event(
                job_id,
                stage="failed",
                message=str(exc),
                payload={"code": code},
            )
            await self._repository.mark_failed(job_id, code=str(code), message=str(exc))
            await self._audit.record(
                event_type="ingest.failed",
                subject_kind="ingest_job",
                subject_id=job_id,
                actor=job.actor,
                correlation_id=job.correlation_id,
                payload={"code": str(code), "message": str(exc)},
            )
            await self._publish_finish(
                event_type="IngestSourceFailed",
                job_id=job_id,
                payload={"code": str(code), "message": str(exc)},
            )
            logger.warning("ingest job failed id=%s code=%s error=%s", job_id, code, exc)
            if job.callback_url:
                await self._fire_webhook(job, status="failed", error_code=str(code), error_message=str(exc))

    async def _publish_finish(self, *, event_type: str, job_id: str, payload: dict[str, Any]) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.ingest_topic,
                event_type=event_type,
                payload={"job_id": job_id, **payload},
            )
        except Exception as exc:
            logger.warning("%s publish failed job_id=%s: %s", event_type, job_id, exc)

    async def _fire_webhook(
        self,
        job: IngestJobRow,
        *,
        status: str,
        source_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return
        payload = {
            "job_id": job.id,
            "status": status,
            "source_id": source_id,
            "error_code": error_code,
            "error_message": error_message,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        headers = {}
        if job.correlation_id:
            headers["X-Correlation-Id"] = job.correlation_id
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(job.callback_url, json=payload, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "webhook delivery failed job_id=%s url=%s: %s",
                job.id,
                job.callback_url,
                exc,
            )


def _serialise_metadata(request: SubmitSourceRequest) -> dict[str, Any]:
    """Round-trip-safe serialisation of ``SubmitSourceRequest.metadata``."""
    meta = request.metadata.model_dump(mode="json") if request.metadata else {}
    return meta
