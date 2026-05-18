# Copyright 2026 Firefly Software Solutions Inc
"""Conversational RAG DTOs.

A conversation is a multi-turn chat session against the canon.
Each turn carries the question, the retrieved citations, the
grounded answer, and the cost / model used. A rolling ``summary``
on the parent conversation row keeps prior context bounded so
long sessions still fit in the model's context window.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from flycanon.interfaces.dtos.knowledge import Citation
from flycanon.interfaces.dtos.query import Hit


class CreateConversationRequest(BaseModel):
    """Open a fresh conversation."""

    title: str | None = Field(default=None, max_length=512)
    actor: str | None = Field(default=None)
    model: str | None = Field(
        default=None,
        description=(
            "Override the configured answer model for this conversation. "
            "Defaults to ``FLYCANON_ANSWER_MODEL``."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationTurn(BaseModel):
    """One turn in a conversation."""

    id: int
    conversation_id: str
    turn_index: int = Field(ge=1)
    question: str
    answer: str | None = Field(default=None)
    citations: list[Hit] = Field(default_factory=list)
    model: str | None = Field(default=None)
    elapsed_ms: int | None = Field(default=None, ge=0)
    no_answer: bool = Field(default=False)
    created_at: datetime


class Conversation(BaseModel):
    """Public view of a conversation + its turn history."""

    id: str
    title: str | None = Field(default=None)
    summary: str | None = Field(
        default=None,
        description=(
            "Rolling synopsis of prior turns. Prepended to the next "
            "turn's prompt so long sessions stay within the model's "
            "context window."
        ),
    )
    actor: str | None = Field(default=None)
    model: str | None = Field(default=None)
    turns: list[ConversationTurn] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateTurnRequest(BaseModel):
    """Append a turn to an existing conversation."""

    question: str = Field(
        min_length=1,
        max_length=4000,
        description="The user's next question. Plain natural language.",
    )
    top_k: int = Field(
        default=8,
        ge=1,
        le=40,
        description="Retrieval window size for this turn.",
    )
    instructions: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional system-level steering appended to the answer prompt.",
    )


class TurnResponse(BaseModel):
    """Synchronous response for the non-streaming turn endpoint."""

    conversation_id: str
    turn: ConversationTurn


class SuggestRequest(BaseModel):
    """Generate suggested follow-up questions.

    Used by chat UIs to render quick-reply chips. Off the same
    LLM that the answer endpoint uses.
    """

    question: str = Field(min_length=1, max_length=4000)
    answer: str | None = Field(
        default=None,
        max_length=8000,
        description="Optional prior answer for richer follow-ups.",
    )
    n: int = Field(default=3, ge=1, le=6)


class SuggestResponse(BaseModel):
    suggestions: list[str] = Field(default_factory=list)
