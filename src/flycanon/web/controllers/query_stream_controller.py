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

"""Streaming + suggestion query endpoints.

Two surfaces complementary to the blocking ``/api/v1/query``:

* ``POST /api/v1/query/stream`` -- SSE stream routed through the
  :class:`AnswerDispatcher` so it honours ``FLYCANON_ANSWER_MODE``.
  In RLM mode (the default) there is no pre-retrieval step: the
  stream emits a ``status`` (``reasoning``) frame per REPL turn then
  the terminal ``final`` frame once the engine returns. In the legacy
  RAG mode the retrieval hits arrive first as ``hit`` frames so the
  UI can render citation chips before the answer is ready, then the
  same ``final`` frame lands. The answer body is a single ``final``
  frame in v1 (the underlying ``output_type`` contract doesn't
  support partial-output streaming yet); future work upgrades this
  to per-token ``delta`` frames once we wire the framework's
  streaming path.

* ``POST /api/v1/query/suggest`` -- emits N suggested follow-up
  questions (chat UI quick-reply chips).
"""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import Body, Valid, post_mapping, request_mapping
from starlette.requests import Request
from starlette.responses import StreamingResponse

from flycanon.core.services.conversations import QuestionSuggester
from flycanon.core.services.query.answer_dispatcher import AnswerDispatcher
from flycanon.core.services.query.answer_service import AnswerService
from flycanon.interfaces.dtos.conversation import SuggestRequest, SuggestResponse
from flycanon.interfaces.dtos.query import AnswerRequest
from flycanon.web.answer_stream import stream_answer_sse
from flycanon.web.conventions import TenantContext, tenant_context_from_request
from flycanon.web.conventions.headers import DEPRECATION_RAG_MESSAGE, HEADER_DEPRECATION


@rest_controller
@request_mapping("/api/v1/query")
class QueryStreamController:
    def __init__(
        self,
        queries: DefaultQueryBus,  # noqa: ARG002 -- future commands
        answer_service: AnswerService,
        answer_dispatcher: AnswerDispatcher,
        suggester: QuestionSuggester,
    ) -> None:
        # ``_answer`` still supplies the retrieval pipeline for the RAG
        # ``hit``-frame path; ``_dispatcher`` selects the engine and runs
        # the answer call so the stream honours ``FLYCANON_ANSWER_MODE``.
        self._answer = answer_service
        self._dispatcher = answer_dispatcher
        self._suggester = suggester

    @post_mapping("/stream")
    async def stream_answer(
        self,
        http_request: Request,
        request: Valid[Body[AnswerRequest]],
    ) -> StreamingResponse:
        """SSE answer stream routed through the answer dispatcher.

        Frames (RAG mode, ``FLYCANON_ANSWER_MODE=rag``):

        * ``event: hit`` -- one frame per retrieved citation
          (UI can render citation badges immediately).
        * ``event: final`` -- terminal frame with the full answer
          + the cited subset + model + elapsed_ms + no_answer flag.

        Frames (RLM mode, the default):

        * ``event: status`` -- one ``reasoning`` frame per REPL turn
          (carrying the turn number + the latest document accessed);
          RLM has no pre-retrieval step so there are no ``hit`` frames.
        * ``event: final`` -- the same terminal frame shape.

        Scope is fixed to the (tenant, workspace) carried by the
        request headers; the :class:`RetrievalService` fails closed
        on missing values.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        # Surface the RAG deprecation to clients; RLM (the default) is silent.
        if self._dispatcher.is_rag:
            headers[HEADER_DEPRECATION] = DEPRECATION_RAG_MESSAGE
        return StreamingResponse(
            stream_answer_sse(
                dispatcher=self._dispatcher,
                answer_service=self._answer,
                request=request,
                ctx=ctx,
            ),
            media_type="text/event-stream",
            headers=headers,
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
