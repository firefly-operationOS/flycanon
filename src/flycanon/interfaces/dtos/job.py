# Copyright 2026 Firefly Software Solutions Inc
"""Async-ingest job DTOs.

The async submit path (``POST /api/v1/sources?mode=async``) returns
an :class:`IngestJob` with ``status=queued`` and a job id. The
caller polls ``GET /api/v1/jobs/{id}`` for the terminal state, or
opens the SSE stream at ``GET /api/v1/jobs/{id}/stream`` for live
progress.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestJob(BaseModel):
    """Public view of a ``canon_ingest_jobs`` row."""

    id: str = Field(description="Stable job id assigned at submit time.")
    status: str = Field(
        description=(
            "Lifecycle state: ``queued`` (worker hasn't picked it up "
            "yet) | ``running`` (intake pipeline executing) | "
            "``succeeded`` (``source_id`` is populated) | "
            "``failed`` (``error_code`` / ``error_message`` carry the "
            "typed failure)."
        ),
        examples=["queued", "running", "succeeded", "failed"],
    )
    source_id: str | None = Field(
        default=None,
        description=(
            "Resolved source id once intake completes. ``null`` while the job is queued or running."
        ),
    )
    attempts: int = Field(ge=0)
    filename: str | None = Field(default=None)
    content_type: str | None = Field(default=None)
    uri: str | None = Field(default=None)
    content_sha256: str | None = Field(default=None)
    actor: str | None = Field(default=None)
    correlation_id: str | None = Field(default=None)
    callback_url: str | None = Field(default=None)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    created_at: datetime
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    updated_at: datetime


class IngestJobEvent(BaseModel):
    """One progress event in the job's lifecycle.

    Emitted by the worker at each pipeline stage. Consumed by the
    SSE endpoint to surface progress, by the API list endpoint for
    audit replay, and -- transitively -- by the audit log.
    """

    id: int = Field(ge=0, description="Monotonic event id (SSE cursor).")
    job_id: str
    stage: str = Field(
        description=(
            "Pipeline stage name. v1 emits: ``queued``, "
            "``normalising``, ``loading``, ``chunking``, "
            "``embedding``, ``indexing``, ``auditing``, ``finished``."
        )
    )
    message: str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class IngestJobsPage(BaseModel):
    items: list[IngestJob]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
