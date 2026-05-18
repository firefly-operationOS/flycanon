# Copyright 2026 Firefly Software Solutions Inc
"""RAG answerer -- hybrid retrieval + LLM answer with citations.

Composes :class:`RetrievalService` with a FireflyAgent answer call.
The agent returns a structured :class:`AnswerOutput` containing the
written answer and the ``chunk_id`` set the model actually cited;
the service hydrates those citations from the retrieval result so
the caller gets the full hit row back (content + score + source_id +
metadata), not just an opaque list of ids.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from flycanon.config import CanonSettings
from flycanon.core.agents import build_agent
from flycanon.core.services.consolidation.prompt_loader import PromptTemplate
from flycanon.core.services.query.search_service import _filters_from_request
from flycanon.core.services.retrieval.retrieval_service import RetrievalService
from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse, Hit

logger = logging.getLogger(__name__)


class AnswerOutput(BaseModel):
    """Structured response shape the answer LLM is asked to produce."""

    answer: str = Field(min_length=1, max_length=8000)
    citation_chunk_ids: list[str] = Field(
        default_factory=list,
        description="``chunk_id`` values the answer actually relied on.",
    )
    no_answer: bool = Field(
        default=False,
        description="True when the chunks do not support a confident answer.",
    )


class AnswerService:
    """RAG answerer driven by the YAML+Jinja2 ``answer`` prompt."""

    def __init__(
        self,
        *,
        retrieval: RetrievalService,
        prompt: PromptTemplate,
        default_model: str,
        fallback_model: str | None,
        settings: CanonSettings,
    ) -> None:
        self._retrieval = retrieval
        self._prompt = prompt
        self._default_model = default_model
        self._fallback_model = fallback_model
        self._settings = settings

    async def answer(
        self,
        request: AnswerRequest,
        *,
        prior_turns: list[tuple[str, str]] | None = None,
    ) -> AnswerResponse:
        """Single-turn (or conversational) RAG answer.

        ``prior_turns`` is an optional ``[(user_text, assistant_text), ...]``
        list. When set, the entries are translated into pydantic-ai
        ``ModelRequest`` / ``ModelResponse`` messages and forwarded to
        the agent as ``message_history`` -- the conversational-context
        path used by :class:`ConversationService`. Single-shot callers
        (``POST /api/v1/query``) leave it empty and the agent runs the
        same way as before.
        """
        start = time.perf_counter()
        result = await self._retrieval.search(
            query=request.question,
            top_k=request.top_k,
            filters=_filters_from_request(  # reuse the search-service helper
                _to_search_request(request)
            ),
        )
        if not result.hits:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return AnswerResponse(
                answer=("There are no documents in the canon that support an answer to that question."),
                citations=[],
                model=request.model or self._default_model,
                elapsed_ms=elapsed_ms,
                no_answer=True,
            )

        system_text, user_text = self._prompt.render(
            question=request.question,
            extra_instructions=request.instructions,
            hits=[
                {
                    "chunk_id": hit.chunk_id,
                    "source_id": hit.source_id,
                    "source_filename": hit.metadata.get("source_filename", ""),
                    "source_title": hit.metadata.get("source_title", ""),
                    "source_kind": hit.metadata.get("source_kind", ""),
                    "content": hit.content,
                    "section_path": hit.metadata.get("section_path", ""),
                    "page": hit.metadata.get("page", ""),
                }
                for hit in result.hits
            ],
        )

        history = _build_message_history(prior_turns)
        model_id = request.model or self._default_model
        try:
            output = await self._call_model(system_text, user_text, model_id, history)
        except Exception as exc:
            if self._fallback_model and self._fallback_model != model_id:
                logger.warning("answer model %s failed (%s); trying fallback", model_id, exc)
                output = await self._call_model(system_text, user_text, self._fallback_model, history)
                model_id = self._fallback_model
            else:
                raise

        # Hydrate citations with the full Hit rows so the caller does
        # not have to make a second call. Goes through the same
        # ``_hit_dto`` mapper as ``/search`` so citations carry the
        # rich source-side fields (filename, title, kind, page,
        # section_path) -- no second ``/sources/{id}`` round-trip
        # needed in the UI.
        from flycanon.core.services.query.search_service import _hit_dto

        hits_by_id = {hit.chunk_id: hit for hit in result.hits}
        citations: list[Hit] = []
        for chunk_id in output.citation_chunk_ids:
            hit = hits_by_id.get(chunk_id)
            if hit is None:
                continue
            citations.append(_hit_dto(hit))

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "answered question=%s hits=%d citations=%d elapsed_ms=%d",
            request.question[:80],
            len(result.hits),
            len(citations),
            elapsed_ms,
        )
        return AnswerResponse(
            answer=output.answer,
            citations=citations,
            model=model_id,
            elapsed_ms=elapsed_ms,
            no_answer=bool(output.no_answer),
        )

    async def _call_model(
        self,
        system_text: str,
        user_text: str,
        model_id: str,
        message_history: list[Any] | None = None,
    ) -> AnswerOutput:
        # The answer stage emits a single grounded response + citation
        # list -- usually shorter than the consolidator's structured
        # output -- but bumping max_tokens past the 4096 provider
        # default still matters for long multi-paragraph answers.
        # Honours the per-stage ``answer_max_output_tokens`` override
        # when set, else the global ``agent_max_output_tokens`` (8192).
        agent = build_agent(
            name="flycanon-answerer",
            model=model_id,
            output_type=AnswerOutput,
            instructions=system_text,
            settings=self._settings,
            max_output_tokens=self._settings.answer_max_output_tokens,
        )
        run_kwargs: dict[str, Any] = {}
        if message_history:
            # pydantic-ai's native ``message_history`` slot. FireflyAgent
            # forwards arbitrary kwargs to the underlying
            # ``pydantic_ai.Agent.run`` so the conversational context
            # lands on the model as alternating user / assistant turns
            # rather than being flattened into the system prompt.
            run_kwargs["message_history"] = message_history
        result = await agent.run(user_text, **run_kwargs)
        output: Any = getattr(result, "output", result)
        if isinstance(output, AnswerOutput):
            return output
        if isinstance(output, dict):
            return AnswerOutput.model_validate(output)
        if isinstance(output, str):
            return AnswerOutput.model_validate_json(output)
        raise RuntimeError(f"unexpected answer output type {type(output).__name__}")


def _build_message_history(
    prior_turns: list[tuple[str, str]] | None,
) -> list[Any] | None:
    """Translate ``[(user, assistant), ...]`` into pydantic-ai messages.

    Returns ``None`` (rather than an empty list) when the caller didn't
    supply any prior turns -- ``None`` skips the message_history slot
    entirely on the agent run.
    """
    if not prior_turns:
        return None
    try:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )
    except ImportError:  # pragma: no cover -- pydantic-ai always present in prod
        return None
    history: list[Any] = []
    for user_text, assistant_text in prior_turns:
        if not user_text and not assistant_text:
            continue
        history.append(ModelRequest(parts=[UserPromptPart(content=user_text or "")]))
        history.append(ModelResponse(parts=[TextPart(content=assistant_text or "")]))
    return history or None


def _to_search_request(request: AnswerRequest):
    """Coerce an :class:`AnswerRequest` into the search filter shape.

    Both DTOs share the same filter base, but the public API uses
    different concrete types; building a tiny shim keeps the helper
    in :mod:`search_service` reusable without forcing controllers to
    pass private types.
    """
    from flycanon.interfaces.dtos.query import SearchRequest

    return SearchRequest(
        query=request.question,
        top_k=request.top_k,
        source_ids=request.source_ids,
        knowledge_item_ids=request.knowledge_item_ids,
        domains=request.domains,
        jurisdictions=request.jurisdictions,
        tags=request.tags,
        statuses=request.statuses,
    )
