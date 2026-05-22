# Copyright 2026 Firefly Software Solutions Inc
"""Agent-tier query endpoints (``/api/v1/agent``).

Three routes mounted under ``/api/v1/agent``:

* ``POST /api/v1/agent/query``        -- RAG answer with citations.
  Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.
* ``POST /api/v1/agent/query/stream`` -- SSE answer stream.
  Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.
* ``POST /api/v1/agent/search``       -- hybrid retrieval (no LLM).
  Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.

All three routes reuse the **same** query handlers / services the
user-tier :class:`QueryController` and :class:`QueryStreamController`
dispatch (:class:`AnswerKnowledgeQuery`, :class:`SearchKnowledgeQuery`,
:class:`AnswerService`) -- the only delta is the auth layer
(``X-Agent-Token`` replaces the operator JWT) and the mandatory
idempotency key on every POST.

The SSE iterator is a verbatim copy of the user-tier streaming
generator; the agent route only prepends the ``X-Agent-Token``
verification step before yielding to the same wire format.
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

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.core.services.query.answer_service import AnswerService
from flycanon.core.services.query.handlers import (
    AnswerKnowledgeQuery,
    SearchKnowledgeQuery,
)
from flycanon.core.services.query.search_service import (
    _filters_from_request,
    _hit_dto,
)
from flycanon.interfaces.dtos.query import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)
from flycanon.web.controllers.agent._helpers import (
    require_idempotency_key,
    verify_agent_token,
)

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/agent")
class AgentQueryController:
    """REST adapter for the agent-tier query surface.

    Wraps the existing query handlers + :class:`AnswerService`
    without duplicating retrieval / answer logic. Same scope
    string (``agent.query:run``) authorises all three routes:
    they share the underlying retrieval pipeline so any caller
    that can run the blocking answer can also run the streaming
    answer and the raw search.
    """

    def __init__(
        self,
        agent_token_service: AgentTokenService,
        queries: DefaultQueryBus,
        answer_service: AnswerService,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._queries = queries
        self._answer = answer_service

    @post_mapping("/query")
    async def answer(
        self,
        http_request: Request,
        request: Valid[Body[AnswerRequest]],
    ) -> AnswerResponse:
        """Grounded RAG answer with explicit citations (agent tier).

        Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.
        Identical wire shape to the user-tier
        ``POST /api/v1/query`` -- the only differences are the
        auth gate and the required idempotency key.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        require_idempotency_key(http_request)
        return await self._queries.query(
            AnswerKnowledgeQuery(
                request=request,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @post_mapping("/query/stream")
    async def stream_answer(
        self,
        http_request: Request,
        request: Valid[Body[AnswerRequest]],
    ) -> StreamingResponse:
        """SSE answer stream (agent tier).

        Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.
        Wire format matches the user-tier
        ``POST /api/v1/query/stream`` byte-for-byte:

        * ``event: hit``   -- one frame per retrieved citation.
        * ``event: final`` -- terminal frame with the full answer.

        The generator is a verbatim copy of the user-tier
        iterator. Token verification + idempotency check happen
        synchronously before the streaming response is returned,
        so authentication failures surface as a normal 4xx
        response (not as an in-stream ``error`` frame).
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        require_idempotency_key(http_request)

        async def _stream():
            started = time.perf_counter()
            # Reuse the answer service's retrieval -> answer
            # pipeline. We emit the hits first so the consuming
            # agent gets citation chips before the model finishes
            # writing.
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

            for hit in retrieval.hits:
                dto = _hit_dto(hit)
                yield _sse_frame("hit", dto.model_dump(mode="json"))

            # Answer in one shot. Per-token streaming is a
            # follow-up once the agentic framework's streaming
            # surface lands; matches the user-tier behaviour.
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

    @post_mapping("/search")
    async def search(
        self,
        http_request: Request,
        request: Valid[Body[SearchRequest]],
    ) -> SearchResponse:
        """Hybrid retrieval over the canon corpus (agent tier).

        Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.
        Identical wire shape to the user-tier
        ``POST /api/v1/search`` -- no LLM call, just BM25 + dense
        retrieval fused via RRF.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        require_idempotency_key(http_request)
        return await self._queries.query(
            SearchKnowledgeQuery(
                request=request,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )


def _sse_frame(event: str, data: dict) -> bytes:
    """Render an SSE frame -- verbatim copy of the user-tier helper."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


__all__ = ["AgentQueryController"]
