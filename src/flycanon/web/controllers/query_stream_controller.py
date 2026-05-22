# Copyright 2026 Firefly Software Solutions Inc
"""Streaming + suggestion query endpoints.

Two surfaces complementary to the blocking ``/api/v1/query``:

* ``POST /api/v1/query/stream`` -- SSE stream. The retrieval hits
  arrive first as ``hit`` frames so the UI can render citation
  chips before the answer is ready. The answer body lands as a
  single ``final`` frame in v1 (the underlying pydantic-ai
  ``output_type`` contract doesn't support partial-output
  streaming yet); future work upgrades this to per-token
  ``delta`` frames once we wire the framework's streaming path.

* ``POST /api/v1/query/suggest`` -- emits N suggested follow-up
  questions (chat UI quick-reply chips).
"""

from __future__ import annotations

import json
import logging
import time

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import Body, Valid, post_mapping, request_mapping
from starlette.requests import Request
from starlette.responses import StreamingResponse

from flycanon.core.services.conversations import QuestionSuggester
from flycanon.core.services.query.answer_service import AnswerService
from flycanon.core.services.query.search_service import _filters_from_request
from flycanon.interfaces.dtos.conversation import SuggestRequest, SuggestResponse
from flycanon.interfaces.dtos.query import AnswerRequest, SearchRequest
from flycanon.web.conventions import TenantContext, tenant_context_from_request

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/query")
class QueryStreamController:
    def __init__(
        self,
        queries: DefaultQueryBus,  # noqa: ARG002 -- future commands
        answer_service: AnswerService,
        suggester: QuestionSuggester,
    ) -> None:
        self._answer = answer_service
        self._suggester = suggester

    @post_mapping("/stream")
    async def stream_answer(
        self,
        http_request: Request,
        request: Valid[Body[AnswerRequest]],
    ) -> StreamingResponse:
        """SSE answer stream.

        Frames:

        * ``event: hit`` -- one frame per retrieved citation
          (UI can render citation badges immediately).
        * ``event: final`` -- terminal frame with the full answer
          + the cited subset + model + elapsed_ms + no_answer flag.

        Scope is fixed to the (tenant, workspace) carried by the
        request headers; the :class:`RetrievalService` fails closed
        on missing values.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)

        async def _stream():
            started = time.perf_counter()
            # Reuse the answer service's retrieval -> answer pipeline.
            # We emit the hits first so the UI gets citation chips
            # before the model finishes writing.
            search_request = SearchRequest(
                query=request.question,
                top_k=request.top_k,
                source_ids=request.source_ids,
                knowledge_item_ids=request.knowledge_item_ids,
                domains=request.domains,
                jurisdictions=request.jurisdictions,
                tags=request.tags,
                statuses=request.statuses,
            )
            retrieval = await self._answer._retrieval.search(  # noqa: SLF001
                query=request.question,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                top_k=request.top_k,
                filters=_filters_from_request(search_request),
            )
            from flycanon.core.services.query.search_service import _hit_dto

            for hit in retrieval.hits:
                dto = _hit_dto(hit)
                yield _sse_frame("hit", dto.model_dump(mode="json"))

            # Answer in one shot. Per-token streaming is a follow-up
            # once the agentic framework's streaming surface lands;
            # in the meantime callers can still render citations
            # immediately and the answer body when it arrives.
            response = await self._answer.answer(
                request,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            yield _sse_frame(
                "final",
                {
                    "answer": response.answer,
                    "citations": [c.model_dump(mode="json") for c in response.citations],
                    "model": response.model,
                    "elapsed_ms": elapsed,
                    "no_answer": response.no_answer,
                },
            )

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @post_mapping("/suggest")
    async def suggest(
        self,
        http_request: Request,
        request: Valid[Body[SuggestRequest]],
    ) -> SuggestResponse:
        """Suggested follow-up questions for chat UI chips."""
        # ctx is parsed for header enforcement -- the suggester is a
        # stateless LLM call that doesn't touch tenant-scoped storage,
        # so we don't thread the scope down further.
        _ctx: TenantContext = tenant_context_from_request(http_request)
        suggestions = await self._suggester.suggest(
            question=request.question,
            answer=request.answer,
            n=request.n,
        )
        return SuggestResponse(suggestions=suggestions)


def _sse_frame(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
