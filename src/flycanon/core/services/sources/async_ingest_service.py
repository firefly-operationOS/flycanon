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
from datetime import UTC, datetime, timedelta
from typing import Any

from pyfly.container import service
from pyfly.eda import EventPublisher
from pyfly.scheduling import scheduled

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
        tenant_id: str,
        workspace_id: str,
        callback_url: str | None = None,
    ) -> IngestJobRow:
        """Persist a job row + broadcast the IngestSourceRequested event.

        ``tenant_id`` and ``workspace_id`` are required -- callers must
        propagate the request-bound :class:`TenantContext`. A missing
        scope is a controller bug, not a silent ``"default"`` fallback.
        """
        job_id = str(uuid.uuid4())
        scope_tenant = tenant_id
        scope_workspace = workspace_id
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
            tenant_id=scope_tenant,
            workspace_id=scope_workspace,
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
            tenant_id=scope_tenant,
            workspace_id=scope_workspace,
        )
        await self._audit.record(
            event_type="ingest.queued",
            subject_kind="ingest_job",
            subject_id=stored.id,
            tenant_id=scope_tenant,
            workspace_id=scope_workspace,
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

        The worker dispatch path receives only the job id in the EDA
        payload, so we read the row through the cross-workspace
        helper to learn the scope. Every subsequent repo call --
        including any that follow on the request path -- is keyed
        by the row's own ``tenant_id`` / ``workspace_id``.

        RLS bypass
        ----------
        Row-level security is enabled on every tenant-scoped table.
        The request flow sets ``app.tenant_id`` /
        ``app.workspace_id`` via :func:`install_tenant_guc_hook`, but
        worker invocations have no :class:`TenantContext` bound -- so
        the GUCs stay empty and RLS would reject the first read.

        Deployment must therefore connect the worker process as a
        Postgres role with ``BYPASSRLS`` (planned: ``flycanon_admin``).
        The application code stays unchanged; only the database role
        differs from the API server (which connects as the unprivileged
        ``flycanon_app`` role). The first cross-workspace query --
        ``get_across_workspaces`` above -- relies on this bypass.
        """
        job = await self._repository.get_across_workspaces(job_id)
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

        running = await self._repository.mark_running(
            job_id,
            lease_seconds=self._settings.ingest_timeout_s,
        )
        if running is None:
            # Another worker holds an active lease OR the job is already
            # terminal. The atomic claim returns nothing in either case
            # and we treat it as an idempotent skip.
            logger.info(
                "ingest job %s could not be claimed (live lease held or terminal) "
                "-- skipping duplicate delivery",
                job_id,
            )
            return
        job = running
        # ``attempts`` from this claim is our lease identity: subsequent
        # mark_succeeded / mark_failed UPDATEs are guarded by it so a
        # poached lease (worker took too long, another replica
        # re-claimed) can't silently overwrite the new owner's state.
        lease_attempts = job.attempts
        # Poison-job guard: once a job has been reclaimed past the
        # attempts ceiling, give up and mark it failed rather than
        # looping forever. A worker that keeps crashing on the same
        # payload would otherwise eat replica budget indefinitely.
        if job.attempts > self._settings.ingest_max_attempts:
            logger.warning(
                "ingest job %s exhausted attempts (%d > %d) -- marking failed",
                job_id,
                job.attempts,
                self._settings.ingest_max_attempts,
            )
            await self._repository.mark_failed(
                job_id,
                code="attempts_exhausted",
                message=f"job aborted after {job.attempts} attempts",
                expected_attempts=lease_attempts,
            )
            return

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
                job_id,
                stage="normalising",
                message="binary normalise + load",
                tenant_id=job.tenant_id,
                workspace_id=job.workspace_id,
            )
            source = await self._intake.submit(
                request=request,
                content=content,
                tenant_id=job.tenant_id,
                workspace_id=job.workspace_id,
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
                tenant_id=job.tenant_id,
                workspace_id=job.workspace_id,
            )
            finalised = await self._repository.mark_succeeded(
                job_id,
                source_id=source.id,
                content_sha256=source.content_sha256,
                expected_attempts=lease_attempts,
            )
            if finalised is None:
                # Lease was poached mid-run. The new owner is the source
                # of truth -- don't publish a second IngestSourceFinished
                # or fire the webhook from this stale path.
                logger.warning(
                    "ingest job %s lease lost mid-run (attempts moved past %d) -- skipping success publish",
                    job_id,
                    lease_attempts,
                )
                return
            await self._audit.record(
                event_type="ingest.succeeded",
                subject_kind="ingest_job",
                subject_id=job_id,
                tenant_id=job.tenant_id,
                workspace_id=job.workspace_id,
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
                tenant_id=job.tenant_id,
                workspace_id=job.workspace_id,
            )
            failed = await self._repository.mark_failed(
                job_id,
                code=str(code),
                message=str(exc),
                expected_attempts=lease_attempts,
            )
            if failed is None:
                # Same lease-poaching guard as the success path: another
                # worker took over while we were running; let them be
                # the canonical source of the failure narrative.
                logger.warning(
                    "ingest job %s lease lost mid-run (attempts moved past %d) -- skipping failure publish",
                    job_id,
                    lease_attempts,
                )
                return
            await self._audit.record(
                event_type="ingest.failed",
                subject_kind="ingest_job",
                subject_id=job_id,
                tenant_id=job.tenant_id,
                workspace_id=job.workspace_id,
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

    # ------------------------------------------------------------------
    # Periodic stuck-job sweep
    # ------------------------------------------------------------------

    @scheduled(fixed_rate=timedelta(seconds=60))
    async def sweep_stuck_jobs(self) -> None:
        """Demote stale ``running`` rows back to ``queued`` + republish.

        ``mark_running`` already re-claims stale rows opportunistically
        when a worker receives a duplicate ``IngestSourceRequested``
        event. This sweep covers the case where the original event
        delivery has been lost (worker crashed before completing AND
        the broker dropped the message): without it, a row sitting at
        ``running`` past its lease has no path back into a worker's
        dispatch loop.

        The sweep is opportunistic, idempotent, and self-rate-limited:

        * ``reclaim_stuck`` uses the same lease threshold
          (``ingest_timeout_s``) as the in-band claim. Rows that have
          legitimate live owners stay at ``running``.
        * Each reclaimed id triggers an ``IngestSourceRequested``
          republish. ``mark_running``'s atomic claim handles the case
          where multiple workers see the republish.
        * The default ``fixed_rate`` of 60s is well below any sensible
          lease budget; a stuck job is observable within one sweep
          interval rather than waiting for an inbound delivery.
        """
        try:
            reclaimed = await self._repository.reclaim_stuck(
                lease_seconds=self._settings.ingest_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 -- swallow so the scheduler loop survives
            logger.warning("stuck-job sweep failed: %s", exc)
            return
        if not reclaimed:
            return
        logger.info("stuck-job sweep reclaimed %d job(s): %s", len(reclaimed), reclaimed)
        for job_id in reclaimed:
            await self._publish_requested(job_id, correlation_id=None)

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
        if not job.callback_url:
            return
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
