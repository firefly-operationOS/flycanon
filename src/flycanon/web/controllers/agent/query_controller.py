# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
from typing import Any

from pydantic import BaseModel
from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import Body, Valid, post_mapping, request_mapping
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.core.services.query.answer_dispatcher import AnswerDispatcher
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
from flycanon.web.conventions.headers import DEPRECATION_RAG_MESSAGE, HEADER_DEPRECATION

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
        answer_dispatcher: AnswerDispatcher,
        idempotency_store: IdempotencyStore,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._queries = queries
        # ``_answer`` still supplies the retrieval pipeline for the RAG
        # ``hit``-frame path; ``_dispatcher`` selects the engine and runs
        # the answer call so the stream honours ``FLYCANON_ANSWER_MODE``.
        self._answer = answer_service
        self._dispatcher = answer_dispatcher
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
        # In the deprecated RAG mode the response carries an
        # ``X-Flycanon-Deprecation`` header; RLM (the default) is silent.
        # The header is a pure signal -- the body is byte-for-byte the
        # same ``AnswerResponse``, so the OpenAPI schema is unchanged.
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.query:run",
        )
        scope = "agent.query:run"
        cached = await check_idempotency_replay(http_request, self._idempotency_store, scope)
        if cached is not None:
            return _maybe_deprecation_response(
                AnswerResponse.model_validate(cached.body), is_rag=self._dispatcher.is_rag
            )
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
        return _maybe_deprecation_response(response, is_rag=self._dispatcher.is_rag)

    @post_mapping("/query/stream")
    async def stream_answer(
        self,
        http_request: Request,
        request: Valid[Body[AnswerRequest]],
    ) -> StreamingResponse:
        """SSE answer stream (agent tier).

        Scope: ``agent.query:run``. Mandatory ``Idempotency-Key``.
        Wire format matches the user-tier
        ``POST /api/v1/query/stream`` byte-for-byte and is routed
        through the same :class:`AnswerDispatcher`:

        * RAG mode -- ``event: hit`` per retrieved citation, then
          ``event: final`` with the full answer.
        * RLM mode (default) -- a single ``event: status``
          (``reasoning``) frame, then ``event: final``.

        The generator mirrors the user-tier
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
                if self._dispatcher.is_rag:
                    # Legacy RAG path: emit the retrieval hits first so the
                    # consuming agent gets citation chips before the model
                    # finishes writing.
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
                else:
                    # RLM (default): no pre-retrieval step. Emit a single
                    # status frame so the consuming agent can show progress
                    # while the engine reasons over the corpus.
                    yield _sse_frame(
                        "status",
                        {"stage": "reasoning", "message": "Reasoning over documents with RLM…"},
                    )

                # Answer in one shot via the dispatcher (RLM or RAG).
                # Per-token streaming is a follow-up once the agentic
                # framework's streaming surface lands; matches the
                # user-tier behaviour.
                response = await self._dispatcher.answer(
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

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        # Surface the RAG deprecation to clients; RLM (the default) is silent.
        if self._dispatcher.is_rag:
            headers[HEADER_DEPRECATION] = DEPRECATION_RAG_MESSAGE
        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers=headers,
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


def _maybe_deprecation_response(response: Any, *, is_rag: bool) -> Any:
    """Attach the RAG deprecation header without altering the body.

    RLM (the default) returns the response untouched so the framework
    serialises it as usual. In RAG mode the same body is wrapped in a
    :class:`JSONResponse` carrying the ``X-Flycanon-Deprecation`` header.
    """
    if not is_rag:
        return response
    body = response.model_dump(mode="json") if isinstance(response, BaseModel) else response
    return JSONResponse(body, headers={HEADER_DEPRECATION: DEPRECATION_RAG_MESSAGE})


__all__ = ["AgentQueryController"]
