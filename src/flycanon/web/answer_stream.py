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

"""Shared answer-SSE stream generator for the query-stream controllers.

Both the user-tier ``POST /api/v1/query/stream`` and the agent-tier
``POST /api/v1/agent/query/stream`` route through the
:class:`AnswerDispatcher` and emit byte-identical SSE frames. The
generator lived as a verbatim copy in each controller; it now lives here
once and both controllers delegate to it.

Frames (RAG mode, ``FLYCANON_ANSWER_MODE=rag``):

* ``event: hit`` -- one frame per retrieved citation (the consumer can
  render citation chips before the model finishes writing).
* ``event: final`` -- terminal frame with the full answer + the cited
  subset + model + elapsed_ms + no_answer flag.

Frames (RLM mode, the default):

* ``event: status`` -- one ``reasoning`` frame per REPL turn (carrying
  the turn number + the latest document accessed); RLM has no
  pre-retrieval step so there are no ``hit`` frames.
* ``event: final`` -- the same terminal frame shape.

A mid-stream failure (retrieval / answer / serialisation) is logged and
surfaced as exactly one terminal ``event: error`` frame, then the stream
closes cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from flycanon.core.services.query.answer_dispatcher import AnswerDispatcher
from flycanon.core.services.query.answer_service import AnswerService
from flycanon.core.services.query.search_service import _filters_from_request, _hit_dto
from flycanon.interfaces.dtos.query import AnswerRequest, SearchRequest
from flycanon.web.conventions import TenantContext

logger = logging.getLogger(__name__)


def _sse_frame(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def stream_answer_sse(
    *,
    dispatcher: AnswerDispatcher,
    answer_service: AnswerService,
    request: AnswerRequest,
    ctx: TenantContext,
) -> AsyncIterator[bytes]:
    """Yield the answer-SSE frames for one request (RAG or RLM).

    Routes through ``dispatcher`` so the stream honours
    ``FLYCANON_ANSWER_MODE``. In RAG mode the retrieval hits arrive first
    as ``hit`` frames; in RLM mode one ``status`` frame is streamed per
    REPL turn. Both modes close with the terminal ``final`` frame, or a
    single ``error`` frame on a mid-stream failure. ``answer_service``
    supplies the retrieval pipeline for the RAG ``hit``-frame path.
    """
    started = time.perf_counter()
    try:
        if dispatcher.is_rag:
            # Legacy RAG path: emit the retrieval hits first so the
            # consumer gets citation chips before the model finishes
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
            retrieval = await answer_service._retrieval.search(  # noqa: SLF001
                query=request.question,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                top_k=request.top_k,
                filters=_filters_from_request(search_request),
            )
            for hit in retrieval.hits:
                dto = _hit_dto(hit)
                yield _sse_frame("hit", dto.model_dump(mode="json"))

            # RAG answers in one shot via the dispatcher; no
            # per-turn progress to stream.
            response = await dispatcher.answer(
                request,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        else:
            # RLM (default): no pre-retrieval step. Stream a live
            # ``status`` frame per REPL turn so the consumer shows progress
            # while the engine reasons over the whole-document corpus.
            #
            # The engine fires ``on_turn`` from its worker thread (the
            # answer runs inside ``asyncio.to_thread``), so the callback
            # marshals each frame onto this event loop via
            # ``call_soon_threadsafe`` into a queue this generator drains.
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def on_turn(turn: int, accessed: list[str]) -> None:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("status", {"stage": "reasoning", "turn": turn, "accessed": accessed[-1:]}),
                )

            task = asyncio.create_task(
                dispatcher.answer(
                    request,
                    tenant_id=ctx.tenant_id,
                    workspace_id=ctx.workspace_id,
                    on_turn=on_turn,
                )
            )
            # Drain queued status frames until the answer task finishes,
            # then flush any frames still queued. The short timeout keeps
            # the loop responsive without busy-spinning.
            while not task.done():
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield _sse_frame(event, data)
            while not queue.empty():
                event, data = queue.get_nowait()
                yield _sse_frame(event, data)

            response = await task
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
        # A mid-stream failure (retrieval / answer / serialisation) used
        # to leave the SSE socket open with no terminal frame so the
        # client hung until idle timeout. Emit one well-formed ``error``
        # frame, log the cause, then close cleanly.
        logger.exception("answer stream failed mid-stream: %s", exc)
        yield _sse_frame(
            "error",
            {"code": "stream_error", "message": str(exc)},
        )
