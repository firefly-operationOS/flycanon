# Copyright 2026 Firefly Software Solutions Inc
"""Async-ingest job read endpoints under ``/api/v1/jobs``.

Three endpoints:

* ``GET /api/v1/jobs/{id}`` -- one-shot job lookup.
* ``GET /api/v1/jobs`` -- paginated listing, optionally filtered
  by status.
* ``GET /api/v1/jobs/{id}/stream`` -- Server-Sent Events stream of
  per-stage progress events. Each frame is ``data:`` + a JSON
  blob carrying ``{id, stage, message, payload, occurred_at}``.
  The stream closes when the job hits a terminal status
  (``succeeded`` / ``failed``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.web import PathVar, QueryParam, get_mapping, request_mapping
from starlette.responses import StreamingResponse

from flycanon.core.services.jobs import (
    GetIngestJobQuery,
    ListIngestJobEventsQuery,
    ListIngestJobsQuery,
)
from flycanon.interfaces.dtos.job import IngestJob, IngestJobsPage

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/jobs")
class JobsController:
    """Read surface for ``canon_ingest_jobs``."""

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("")
    async def list_jobs(
        self,
        status: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> IngestJobsPage:
        """Paginated listing of async ingest jobs."""
        return await self._queries.query(
            ListIngestJobsQuery(
                status=status or None,
                limit=limit,
                offset=offset,
            )
        )

    @get_mapping("/{job_id}")
    async def get_job(self, job_id: PathVar[str]) -> IngestJob:
        """Single-shot lookup. Polling target for callers that
        prefer ``GET`` over SSE."""
        row = await self._queries.query(GetIngestJobQuery(job_id=job_id))
        if row is None:
            raise ResourceNotFoundException(f"ingest job {job_id!r} not found")
        return row

    @get_mapping("/{job_id}/stream")
    async def stream_job(
        self,
        job_id: PathVar[str],
        poll_interval_ms: QueryParam[int] = 500,
    ) -> StreamingResponse:
        """SSE stream of progress events.

        Each ``data:`` frame is a JSON blob. The stream closes when
        the job reaches a terminal state. Implementation polls the
        ``canon_ingest_job_events`` table (cursor = max event id
        seen) and the ``canon_ingest_jobs.status`` column at
        ``poll_interval_ms`` cadence -- this avoids holding a
        long-lived DB connection per stream and works under
        pgbouncer transaction-mode pooling.
        """
        # First check the job exists -- 404 propagates as a proper
        # HTTP error before the streaming response body opens.
        job = await self._queries.query(GetIngestJobQuery(job_id=job_id))
        if job is None:
            raise ResourceNotFoundException(f"ingest job {job_id!r} not found")

        async def _stream() -> AsyncIterator[bytes]:
            cursor: int | None = None
            interval = max(0.05, min(5.0, poll_interval_ms / 1000.0))
            terminal = {"succeeded", "failed"}
            # Open with a snapshot of the current state so the
            # client gets the job header immediately.
            current = await self._queries.query(GetIngestJobQuery(job_id=job_id))
            if current is not None:
                yield _sse_event(
                    "status",
                    {
                        "id": current.id,
                        "status": current.status,
                        "attempts": current.attempts,
                    },
                )
            while True:
                events = await self._queries.query(
                    ListIngestJobEventsQuery(job_id=job_id, after_id=cursor)
                )
                for event in events:
                    cursor = event.id
                    yield _sse_event(
                        "event",
                        {
                            "id": event.id,
                            "stage": event.stage,
                            "message": event.message,
                            "payload": event.payload,
                            "occurred_at": event.occurred_at.isoformat()
                            if event.occurred_at
                            else None,
                        },
                    )
                current = await self._queries.query(GetIngestJobQuery(job_id=job_id))
                if current is None:
                    break
                if current.status in terminal:
                    # One more status frame so the client sees the
                    # terminal state alongside the final event.
                    yield _sse_event(
                        "status",
                        {
                            "id": current.id,
                            "status": current.status,
                            "source_id": current.source_id,
                            "error_code": current.error_code,
                            "error_message": current.error_message,
                        },
                    )
                    break
                await asyncio.sleep(interval)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


def _sse_event(event_type: str, data: dict) -> bytes:
    """Format a single SSE frame.

    The ``event:`` line lets EventSource consumers route distinct
    frame types to separate handlers
    (``es.addEventListener('status', ...)``); the ``data:`` line
    carries the JSON payload.
    """
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
