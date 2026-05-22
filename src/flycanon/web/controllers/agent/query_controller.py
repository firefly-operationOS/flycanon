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
    check_idempotency_replay,
    require_idempotency_key,
    store_idempotent_response,
    verify_agent_token,
)
from flycanon.web.conventions import IdempotencyStore

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
        idempotency_store: IdempotencyStore,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._queries = queries
        self._answer = answer_service
        self._idempotency_store = idempotency_store

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

        A replayed POST (same ``Idempotency-Key`` + same tenant
        within the store TTL) returns the cached
        :class:`AnswerResponse` re-hydrated from the stored JSON
        body, without re-running retrieval + the LLM call.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        scope = "agent.query:run"
        cached = await check_idempotency_replay(http_request, self._idempotency_store, scope)
        if cached is not None:
            return AnswerResponse.model_validate(cached.body)
        response = await self._queries.query(
            AnswerKnowledgeQuery(
                request=request,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        await store_idempotent_response(
            http_request,
            self._idempotency_store,
            scope,
            status=200,
            response=response,
        )
        return response

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
        iterator. Token verification + idempotency-header check
        happen synchronously before the streaming response is
        returned, so authentication failures surface as a normal
        4xx response (not as an in-stream ``error`` frame).

        Replay dedup is **intentionally skipped** for the SSE
        endpoint -- streaming responses cannot be replayed
        deterministically (frame ordering, hit ranking, and LLM
        non-determinism would all need to be captured), and the
        client is expected to handle a partial stream by reading
        the terminal ``final`` / ``error`` frame. Only the
        ``Idempotency-Key`` header *presence* is enforced; no
        store interaction occurs.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        require_idempotency_key(http_request)

        async def _stream():
            started = time.perf_counter()
            try:
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
            except Exception as exc:  # noqa: BLE001 -- terminal error frame
                # A mid-stream failure (retrieval / answer / serialisation)
                # used to leave the SSE socket open with no terminal frame
                # so the client hung until idle timeout. Emit one well-
                # formed ``error`` frame, log the cause, then close cleanly.
                logger.exception("agent query stream failed mid-stream: %s", exc)
                yield _sse_frame(
                    "error",
                    {"code": "stream_error", "message": str(exc)},
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

        Replays under the same ``(tenant, scope, key)`` triple are
        cached for the store TTL -- the retrieval pipeline is not
        re-run. The replay-dedup ``scope`` is the route-specific
        ``agent.search:run`` so a key reused on ``/query`` and
        ``/search`` does **not** collide, even though both routes
        share the same auth scope ``agent.query:run``.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        scope = "agent.search:run"
        cached = await check_idempotency_replay(http_request, self._idempotency_store, scope)
        if cached is not None:
            return SearchResponse.model_validate(cached.body)
        response = await self._queries.query(
            SearchKnowledgeQuery(
                request=request,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        await store_idempotent_response(
            http_request,
            self._idempotency_store,
            scope,
            status=200,
            response=response,
        )
        return response


def _sse_frame(event: str, data: dict) -> bytes:
    """Render an SSE frame -- verbatim copy of the user-tier helper."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


__all__ = ["AgentQueryController"]
