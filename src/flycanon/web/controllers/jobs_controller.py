# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Async-ingest job read endpoints under ``/api/v1/ingest-jobs``.

Three endpoints:

* ``GET /api/v1/ingest-jobs/{id}`` -- one-shot job lookup.
* ``GET /api/v1/ingest-jobs`` -- paginated listing, optionally filtered
  by status.
* ``GET /api/v1/ingest-jobs/{id}/stream`` -- Server-Sent Events stream of
  per-stage progress events. Each ``event`` frame carries an SSE
  ``id:`` line (the monotonic event id) and a ``data:`` JSON blob
  ``{id, stage, message, payload, occurred_at}``. The stream closes
  when the job hits a terminal status (``succeeded`` / ``failed``).
  A reconnecting client resumes after the last event it saw with
  ``?after_id=<id>`` or the standard ``Last-Event-ID`` request header
  (``EventSource`` sends the latter automatically on reconnect).
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
from starlette.requests import Request
from starlette.responses import StreamingResponse

from flycanon.core.services.jobs import (
    GetIngestJobQuery,
    ListIngestJobEventsQuery,
    ListIngestJobsQuery,
)
from flycanon.interfaces.dtos.job import IngestJob, IngestJobsPage
from flycanon.web.conventions import TenantContext, tenant_context_from_request

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/ingest-jobs")
class JobsController:
    """Read surface for ``canon_ingest_jobs``."""

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("")
    async def list_jobs(
        self,
        http_request: Request,
        status: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> IngestJobsPage:
        """Paginated listing of async ingest jobs."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._queries.query(
            ListIngestJobsQuery(
                status=status or None,
                limit=limit,
                offset=offset,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @get_mapping("/{job_id}")
    async def get_job(
        self,
        http_request: Request,
        job_id: PathVar[str],
    ) -> IngestJob:
        """Single-shot lookup. Polling target for callers that
        prefer ``GET`` over SSE."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        row = await self._queries.query(
            GetIngestJobQuery(
                job_id=job_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        if row is None:
            raise ResourceNotFoundException(f"ingest job {job_id!r} not found")
        return row

    @get_mapping("/{job_id}/stream")
    async def stream_job(
        self,
        http_request: Request,
        job_id: PathVar[str],
        poll_interval_ms: QueryParam[int] = 500,
        after_id: QueryParam[int] = 0,
    ) -> StreamingResponse:
        """SSE stream of progress events.

        Each ``event`` frame carries ``id:`` + ``data:``; ``status``
        frames carry ``data:`` only. The stream closes when the job
        reaches a terminal state. Implementation polls the
        ``canon_ingest_job_events`` table (cursor = max event id
        seen) and the ``canon_ingest_jobs.status`` column at
        ``poll_interval_ms`` cadence -- this avoids holding a
        long-lived DB connection per stream and works under
        pgbouncer transaction-mode pooling.

        Resume: ``after_id`` (query) or ``Last-Event-ID`` (header)
        seeds the cursor so a reconnect replays only events with a
        greater id. Without it the cursor started at ``None`` and every
        reconnect replayed the whole history, which is what
        ``docs/api-reference.md`` promised ``after_id`` would prevent
        long before the parameter existed (26.7.1 closes that gap). The
        query parameter wins when both are present; a value of ``0``
        (the default) means "from the beginning".
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        resume_from = resolve_resume_cursor(after_id, http_request.headers.get("Last-Event-ID"))
        # First check the job exists -- 404 propagates as a proper
        # HTTP error before the streaming response body opens.
        job = await self._queries.query(
            GetIngestJobQuery(
                job_id=job_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        if job is None:
            raise ResourceNotFoundException(f"ingest job {job_id!r} not found")

        async def _stream() -> AsyncIterator[bytes]:
            cursor: int | None = resume_from
            interval = max(0.05, min(5.0, poll_interval_ms / 1000.0))
            terminal = {"succeeded", "failed"}
            # Open with a snapshot of the current state so the
            # client gets the job header immediately.
            current = await self._queries.query(
                GetIngestJobQuery(
                    job_id=job_id,
                    tenant_id=ctx.tenant_id,
                    workspace_id=ctx.workspace_id,
                )
            )
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
                    ListIngestJobEventsQuery(
                        job_id=job_id,
                        after_id=cursor,
                        tenant_id=ctx.tenant_id,
                        workspace_id=ctx.workspace_id,
                    )
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
                            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                        },
                        event_id=event.id,
                    )
                current = await self._queries.query(
                    GetIngestJobQuery(
                        job_id=job_id,
                        tenant_id=ctx.tenant_id,
                        workspace_id=ctx.workspace_id,
                    )
                )
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


def resolve_resume_cursor(after_id: int, last_event_id: str | None) -> int | None:
    """Pick the resume cursor from ``?after_id=`` or ``Last-Event-ID``.

    ``after_id > 0`` wins. Otherwise a parseable, positive
    ``Last-Event-ID`` is used -- browsers' ``EventSource`` sends the
    ``id:`` of the last frame it received on automatic reconnect, so
    honouring it gives resume-for-free to every browser client without
    any URL rewriting. Anything else (absent, ``0``, negative, not an
    integer) means "from the beginning" and returns ``None``.
    """
    if after_id and after_id > 0:
        return int(after_id)
    if last_event_id:
        try:
            parsed = int(last_event_id.strip())
        except ValueError:
            return None
        if parsed > 0:
            return parsed
    return None


def _sse_event(event_type: str, data: dict, *, event_id: int | None = None) -> bytes:
    """Format a single SSE frame.

    The ``event:`` line lets EventSource consumers route distinct
    frame types to separate handlers
    (``es.addEventListener('status', ...)``); the optional ``id:`` line
    is what the browser echoes back as ``Last-Event-ID`` on reconnect;
    the ``data:`` line carries the JSON payload.
    """
    head = f"event: {event_type}\n"
    if event_id is not None:
        head += f"id: {event_id}\n"
    return f"{head}data: {json.dumps(data)}\n\n".encode()
