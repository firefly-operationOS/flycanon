# Copyright 2026 Firefly Software Solutions Inc
"""``IngestJobRow`` -> :class:`IngestJob` mapper."""

from __future__ import annotations

from flycanon.interfaces.dtos.job import IngestJob
from flycanon.models.entities.ingest_job import IngestJobRow


def to_ingest_job(row: IngestJobRow) -> IngestJob:
    return IngestJob(
        id=row.id,
        status=row.status,
        source_id=row.source_id,
        attempts=row.attempts or 0,
        filename=row.filename,
        content_type=row.content_type,
        uri=row.uri,
        content_sha256=row.content_sha256,
        actor=row.actor,
        correlation_id=row.correlation_id,
        callback_url=row.callback_url,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
    )
