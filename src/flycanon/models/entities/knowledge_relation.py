# Copyright 2026 Firefly Software Solutions Inc
"""``canon_knowledge_relations`` -- typed edges between knowledge items.

Each row carries a directed link ``from_item_id -> to_item_id`` with
a ``kind`` discriminator (``related``, ``depends_on``,
``conflicts_with``, ``replaces``) and optional metadata (``note``,
``actor``, ``since_version``). The graph is read by the provenance
endpoint + the knowledge-graph visualisation endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class KnowledgeRelationRow(Base):
    __tablename__ = "canon_knowledge_relations"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    from_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    since_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "from_item_id",
            "to_item_id",
            "kind",
            name="uq_canon_knowledge_relations_from_to_kind",
        ),
    )
