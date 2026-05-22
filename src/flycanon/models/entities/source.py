# Copyright 2026 Firefly Software Solutions Inc
"""``canon_sources`` -- raw inbound artefacts.

The original bytes are not stored; only the metadata, the content hash
(for idempotency + change detection), and the per-source chunk count.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class SourceRow(Base):
    __tablename__ = "canon_sources"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        doc="Stable string UUID used as the public source id.",
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

    # The source kind drives loader selection. ``unknown`` is the
    # placeholder until the loader fingerprints the bytes.
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    # Provenance.
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Output.
    n_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Inputs / extra context.
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bookkeeping.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # SHA-256 is the idempotency anchor: two uploads of the same
        # bytes hit the same row. The partial index keeps the
        # constraint useful even on Postgres where ``NULL`` would
        # otherwise be globally allowed.
        Index(
            "uq_canon_sources_content_sha256",
            "content_sha256",
            unique=True,
            postgresql_where=(content_sha256.is_not(None)),
            sqlite_where=(content_sha256.is_not(None)),
        ),
        Index("ix_canon_sources_tenant_workspace", "tenant_id", "workspace_id"),
    )
