# Copyright 2026 Firefly Software Solutions Inc
"""``canon_citations`` -- (knowledge_version, chunk) edges.

Each row anchors one claim on a knowledge version to one source chunk.
Citation rows are immutable; rebuilding the citation set for a new
version means inserting a fresh row per claim and never updating
existing ones. The denormalised ``source_id`` is a convenience for
the read path so provenance lookups avoid a second join.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
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


class CitationRow(Base):
    __tablename__ = "canon_citations"

    id: Mapped[str] = mapped_column(
        String(36),
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
    knowledge_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("canon_knowledge_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("canon_chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("canon_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_canon_citations_version_chunk",
            "knowledge_version_id",
            "chunk_id",
        ),
        Index("ix_canon_citations_tenant_workspace", "tenant_id", "workspace_id"),
    )
