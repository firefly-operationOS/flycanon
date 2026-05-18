# Copyright 2026 Firefly Software Solutions Inc
"""``canon_knowledge_items`` -- canonical, versioned knowledge units.

The item row tracks the current state -- which version is canonical,
which status it sits in, its domain / jurisdiction header. Every
change to the actual content lands as a new
:class:`KnowledgeVersionRow`; the item row never gets in-place
mutations to ``title`` / ``body`` / ``summary`` (those live on the
version), only to its lifecycle status and pointers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class KnowledgeItemRow(Base):
    __tablename__ = "canon_knowledge_items"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="GLOBAL", index=True)

    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Lifecycle pointers.
    superseded_by_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_canon_knowledge_items_domain_status", "domain", "status"),
    )
