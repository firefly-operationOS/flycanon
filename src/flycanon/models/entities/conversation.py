# Copyright 2026 Firefly Software Solutions Inc
"""``canon_conversations`` + ``canon_conversation_turns``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class ConversationRow(Base):
    __tablename__ = "canon_conversations"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ConversationTurnRow(Base):
    __tablename__ = "canon_conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("canon_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_answer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "turn_index",
            name="uq_canon_conversation_turns_conv_turn",
        ),
        Index(
            "ix_canon_conversation_turns_conv_created",
            "conversation_id",
            "created_at",
        ),
    )
