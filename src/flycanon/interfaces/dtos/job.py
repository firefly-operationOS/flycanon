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
    attempts: int = Field(
        ge=0,
        description="How many times the worker has tried this job (bumped on each retry).",
    )
    filename: str | None = Field(
        default=None,
        description="Original filename declared at submit time, when available.",
    )
    content_type: str | None = Field(
        default=None,
        description="Declared content type from the submit request (server still re-sniffs the magic bytes).",
    )
    uri: str | None = Field(
        default=None,
        description="Optional source URI captured at submit (mirrors ``SourceRecord.uri``).",
    )
    content_sha256: str | None = Field(
        default=None,
        description="SHA-256 of the raw payload; null until the worker has hashed the content.",
    )
    actor: str | None = Field(
        default=None,
        description="Caller identity captured at submit. Surfaces on the audit + billing trails.",
    )
    correlation_id: str | None = Field(
        default=None,
        description=(
            "W3C correlation id from the originating request -- pivot back to the audit log with this."
        ),
    )
    callback_url: str | None = Field(
        default=None,
        description=(
            "Optional webhook URL the worker POSTs to on completion / failure. "
            "Receives a ``{job_id, status, source_id, error_code, error_message, occurred_at}`` body."
        ),
    )
    error_code: str | None = Field(
        default=None,
        description="Stable error code matching the RFC 7807 ``code`` field. Populated on ``failed``.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable failure detail. Populated on ``failed``.",
    )
    created_at: datetime = Field(description="UTC timestamp of submit.")
    started_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the worker picked the job up (``null`` while still queued).",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the terminal transition (``succeeded`` or ``failed``).",
    )
    updated_at: datetime = Field(description="UTC timestamp of the last row mutation.")


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
    message: str | None = Field(
        default=None,
        description="Optional human-readable note. Surfaced verbatim in SSE / list views.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Stage-specific structured payload (e.g. ``{chunks: 42}`` on chunking).",
    )
    occurred_at: datetime = Field(description="UTC timestamp the event was emitted.")


class IngestJobsPage(BaseModel):
    """Paged view of recent ingest jobs (``GET /api/v1/jobs``)."""

    items: list[IngestJob] = Field(description="One row per job in the requested page.")
    total: int = Field(ge=0, description="Total jobs matching the filter (before pagination).")
    offset: int = Field(ge=0, description="0-based offset of the first row in ``items``.")
    limit: int = Field(ge=1, le=500, description="Page size requested by the caller (1-500).")
