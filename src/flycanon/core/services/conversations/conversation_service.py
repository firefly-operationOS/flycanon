# Copyright 2026 Firefly Software Solutions Inc
"""Conversational RAG orchestrator.

Owns the chat session lifecycle:

* :meth:`create` -- open a fresh conversation row.
* :meth:`append_turn` -- run a turn end-to-end (retrieve →
  answer → persist).
* :meth:`get` -- fetch the session + its turn history.

The per-turn answer call goes through the existing
:class:`AnswerService` (PR-D's reranker + expander apply
transparently). The rolling ``summary`` field on the
conversation row is updated after every turn so future turns
stay within the model's context window even after 20+ turns.

**Memory architecture.** Conversations are persisted to
``canon_conversations`` + ``canon_conversation_turns`` (Postgres
in prod, SQLite in tests) so they survive restarts -- the
in-process ``fireflyframework_agentic.memory.ConversationMemory``
would not fit a horizontally-scaled microservice. We *do*
leverage agentic where it makes sense, though: the last few prior
turns are translated into pydantic-ai ``ModelRequest`` /
``ModelResponse`` messages and forwarded to the answer agent via
the native ``message_history`` slot (see
:class:`flycanon.core.services.query.AnswerService`). The rolling
summary continues to ride on the system-instructions slot so
older turns the model wouldn't otherwise see still inform the
answer.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from pyfly.container import service

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.query.answer_service import AnswerService
from flycanon.interfaces.dtos.conversation import (
    Conversation,
    ConversationTurn,
    CreateConversationRequest,
    CreateTurnRequest,
)
from flycanon.interfaces.dtos.query import AnswerRequest, Hit
from flycanon.models.entities.conversation import (
    ConversationRow,
    ConversationTurnRow,
)
from flycanon.models.repositories.conversation_repository import (
    ConversationRepository,
)

logger = logging.getLogger(__name__)


class ConversationNotFound(Exception):
    code = "conversation_not_found"
    http_status = 404

    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"conversation {conversation_id!r} not found")


@service
class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        answer_service: AnswerService,
        audit: AuditService,
        settings: CanonSettings,
    ) -> None:
        self._repository = repository
        self._answer = answer_service
        self._audit = audit
        self._settings = settings

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create(
        self,
        request: CreateConversationRequest,
        *,
        correlation_id: str | None = None,
    ) -> ConversationRow:
        row = ConversationRow(
            id=str(uuid.uuid4()),
            title=request.title,
            actor=request.actor,
            model=request.model or self._settings.answer_model,
            metadata_json=dict(request.metadata or {}),
        )
        stored = await self._repository.add(row)
        await self._audit.record(
            event_type="conversation.created",
            subject_kind="conversation",
            subject_id=stored.id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={"title": request.title, "model": stored.model},
        )
        logger.info("conversation created id=%s actor=%s", stored.id, request.actor)
        return stored

    async def append_turn(
        self,
        conversation_id: str,
        request: CreateTurnRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[ConversationRow, ConversationTurnRow]:
        conversation = await self._repository.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)

        prior = await self._repository.list_turns(conversation_id)
        # Two-pronged context delivery:
        # * Older context lives in the rolling ``summary`` and rides on
        #   the system-instructions slot (alongside any caller-supplied
        #   ``instructions``).
        # * The last two turns ride on pydantic-ai's native
        #   ``message_history`` slot via ``prior_turns``. The model
        #   then sees them as alternating user / assistant messages,
        #   which produces noticeably better continuity on Claude /
        #   GPT than flattening everything into one system prompt.
        steering = self._build_steering(
            conversation=conversation,
            extra_instructions=request.instructions,
        )
        message_history = self._recent_turns_for_history(prior)

        answer_response = await self._answer.answer(
            AnswerRequest(
                question=request.question,
                top_k=request.top_k,
                instructions=steering,
                model=conversation.model,
            ),
            prior_turns=message_history,
        )

        turn_row = ConversationTurnRow(
            conversation_id=conversation_id,
            turn_index=await self._repository.next_turn_index(conversation_id),
            question=request.question,
            answer=answer_response.answer,
            citations_json=[cit.model_dump(mode="json") for cit in answer_response.citations],
            model=answer_response.model,
            elapsed_ms=answer_response.elapsed_ms,
            no_answer=answer_response.no_answer,
        )
        turn_stored = await self._repository.add_turn(turn_row)

        # Bounded rolling summary -- one-line per turn, capped at 16
        # turns. Beyond that the earliest turn drops out. For
        # production deployments that want a semantic summary, a
        # follow-up can plug an LLM-driven summarisation step here.
        conversation.summary = self._next_summary(conversation, turn_stored)
        await self._repository.update(conversation)

        await self._audit.record(
            event_type="conversation.turn_appended",
            subject_kind="conversation",
            subject_id=conversation_id,
            actor=conversation.actor,
            correlation_id=correlation_id,
            payload={
                "turn_index": turn_stored.turn_index,
                "no_answer": turn_stored.no_answer,
                "n_citations": len(answer_response.citations),
            },
        )
        return conversation, turn_stored

    async def get(self, conversation_id: str) -> Conversation:
        conv = await self._repository.get(conversation_id)
        if conv is None:
            raise ConversationNotFound(conversation_id)
        turns = await self._repository.list_turns(conversation_id)
        return Conversation(
            id=conv.id,
            title=conv.title,
            summary=conv.summary,
            actor=conv.actor,
            model=conv.model,
            turns=[_turn_to_dto(t) for t in turns],
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_steering(
        *,
        conversation: ConversationRow,
        extra_instructions: str | None,
    ) -> str:
        """Compose the system-instructions slot for this turn.

        Layers, in order:

        1. Caller-supplied ``instructions`` (highest precedence).
        2. Rolling summary -- one-line synopsis of every turn so
           older context isn't lost once it falls outside the
           ``message_history`` window.

        The last few turns no longer ride here -- they are forwarded
        through ``message_history`` instead, see
        :meth:`_recent_turns_for_history`.
        """
        lines: list[str] = []
        if extra_instructions:
            lines.append(extra_instructions.strip())
        if conversation.summary:
            lines.append("Prior conversation summary:\n" + conversation.summary.strip())
        return "\n\n".join(line for line in lines if line)

    @staticmethod
    def _recent_turns_for_history(
        prior_turns: Sequence[ConversationTurnRow],
        *,
        max_turns: int = 2,
    ) -> list[tuple[str, str]]:
        """Convert the tail of the turn log into pydantic-ai-friendly
        ``(user, assistant)`` pairs.

        Empty / errored turns are dropped so the model never sees a
        half-finished exchange. ``max_turns`` caps the slice -- two
        is enough for anaphora resolution ("and what about Y?")
        without ballooning the prompt.
        """
        out: list[tuple[str, str]] = []
        for turn in prior_turns[-max_turns:]:
            if not turn.question or not turn.answer:
                continue
            out.append((turn.question, turn.answer))
        return out

    @staticmethod
    def _next_summary(
        conversation: ConversationRow,
        new_turn: ConversationTurnRow,
        *,
        max_lines: int = 16,
    ) -> str:
        prior = (conversation.summary or "").splitlines()
        prior.append(
            f"- T{new_turn.turn_index}: {new_turn.question[:120]} -> {(new_turn.answer or '')[:200]}"
        )
        return "\n".join(prior[-max_lines:])


def _turn_to_dto(row: ConversationTurnRow) -> ConversationTurn:
    citations = [Hit.model_validate(c) for c in (row.citations_json or [])]
    return ConversationTurn(
        id=row.id,
        conversation_id=row.conversation_id,
        turn_index=row.turn_index,
        question=row.question,
        answer=row.answer,
        citations=citations,
        model=row.model,
        elapsed_ms=row.elapsed_ms,
        no_answer=row.no_answer,
        created_at=row.created_at,
    )
