# Copyright 2026 Firefly Software Solutions Inc
"""``canon_ingest_jobs`` + ``canon_ingest_job_events`` -- async ingest tracking."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class IngestJobRow(Base):
    __tablename__ = "canon_ingest_jobs"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'default'"),
        default="default",
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'default'"),
        default="default",
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_canon_ingest_jobs_tenant_workspace", "tenant_id", "workspace_id"),
    )


class IngestJobEventRow(Base):
    """Per-stage progress event a worker emits during an ingest job.

    The SSE streaming endpoint polls this table so a UI can render
    real progress instead of a spinner. The table is intentionally
    append-only -- a finished job's event trail stays alongside the
    final ``IngestJobRow.status`` for audit replay.
    """

    __tablename__ = "canon_ingest_job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'default'"),
        default="default",
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'default'"),
        default="default",
        index=True,
    )
    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("canon_ingest_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_canon_ingest_job_events_job_occurred",
            "job_id",
            "occurred_at",
        ),
        Index(
            "ix_canon_ingest_job_events_tenant_workspace",
            "tenant_id",
            "workspace_id",
        ),
    )
