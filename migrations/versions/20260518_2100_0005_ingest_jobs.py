# Copyright 2026 Firefly Software Solutions Inc
"""Async-ingestion job tracking + per-job event records.

Revision ID: 0005_ingest_jobs
Revises: 0004_knowledge_relations
Create Date: 2026-05-18 21:00:00 UTC

The synchronous ``POST /api/v1/sources`` blocks the request until
the whole intake pipeline (normalise -> load -> chunk -> embed ->
index) completes. For a 200-MB archive of scanned PDFs that's
minutes; the request times out, the client retries, the same
bytes get processed twice. This migration lands the persistence
layer for **async ingestion**:

* ``canon_ingest_jobs`` -- one row per async submit. Tracks the
  lifecycle (queued -> running -> succeeded / failed), attempts,
  the resolved ``source_id`` once the row is materialised, and
  the optional caller webhook target.
* ``canon_ingest_job_events`` -- per-stage progress events the
  worker emits via :meth:`IngestJobRepository.append_event`.
  The SSE endpoint (``GET /api/v1/jobs/{id}/stream``) polls
  these so a UI can render a real progress bar instead of a
  spinner.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_ingest_jobs"
down_revision = "0004_knowledge_relations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canon_ingest_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "source_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filename", sa.String(length=512), nullable=True),
        sa.Column("content_type", sa.String(length=256), nullable=True),
        sa.Column("uri", sa.String(length=2048), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("callback_url", sa.String(length=2048), nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_canon_ingest_jobs_status",
        "canon_ingest_jobs",
        ["status"],
    )
    op.create_index(
        "ix_canon_ingest_jobs_source_id",
        "canon_ingest_jobs",
        ["source_id"],
    )

    op.create_table(
        "canon_ingest_job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.String(length=64),
            sa.ForeignKey("canon_ingest_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON, nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_canon_ingest_job_events_job_occurred",
        "canon_ingest_job_events",
        ["job_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canon_ingest_job_events_job_occurred",
        table_name="canon_ingest_job_events",
    )
    op.drop_table("canon_ingest_job_events")
    op.drop_index("ix_canon_ingest_jobs_source_id", table_name="canon_ingest_jobs")
    op.drop_index("ix_canon_ingest_jobs_status", table_name="canon_ingest_jobs")
    op.drop_table("canon_ingest_jobs")
