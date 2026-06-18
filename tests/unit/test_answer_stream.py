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

"""Direct coverage for the shared ``stream_answer_sse`` generator.

The user-tier and agent-tier stream controllers both delegate to
:func:`flycanon.web.answer_stream.stream_answer_sse`. The controller-
level tests already exercise the end-to-end routes; these tests call the
shared generator directly with light fakes to lock its three branches:

* RLM mode -- one ``status`` frame per REPL turn, then ``final``.
* RAG mode -- one ``hit`` frame per retrieved citation, then ``final``.
* a failing answer -- exactly one terminal ``error`` frame.

No network / LLM calls -- the dispatcher and retrieval are fakes.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse, Hit
from flycanon.web.answer_stream import stream_answer_sse

# ---------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------


class _Ctx:
    """Minimal :class:`TenantContext` stand-in (only the scope fields)."""

    def __init__(self) -> None:
        self.tenant_id = "acme"
        self.workspace_id = "ws-1"


class _FakeDispatcher:
    """Canned :class:`AnswerDispatcher`; fires ``on_turn`` in RLM mode."""

    def __init__(self, *, is_rag: bool, response: AnswerResponse, turns: int = 2) -> None:
        self.is_rag = is_rag
        self._response = response
        self._turns = turns

    async def answer(self, request, *, tenant_id=None, workspace_id=None, on_turn=None):
        if on_turn is not None:
            for turn in range(1, self._turns + 1):
                on_turn(turn, [f"doc-{turn}"])
        return self._response


class _FailingDispatcher:
    """Dispatcher whose answer call raises mid-stream."""

    def __init__(self, *, is_rag: bool) -> None:
        self.is_rag = is_rag

    async def answer(self, request, *, tenant_id=None, workspace_id=None, on_turn=None):
        raise RuntimeError("answer boom")


class _Retrieval:
    """Retrieval double returning a fixed hit list off ``.hits``."""

    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits
        self.search_calls = 0

    async def search(self, **kwargs):
        self.search_calls += 1
        return self


def _hit(content: str) -> Hit:
    return Hit(
        chunk_id="src-1#p1",
        source_id="src-1",
        source_filename="doc.pdf",
        source_title="Doc",
        source_kind="pdf",
        page=1,
        content=content,
        score=1.0,
        section_path=None,
        metadata={},
    )


def _answer_request() -> AnswerRequest:
    return AnswerRequest(question="What is the scope?", top_k=3)


def _answer_response() -> AnswerResponse:
    return AnswerResponse(
        answer="The scope is acme/ws-1.",
        citations=[_hit("cited snippet")],
        model="rlm-root",
        elapsed_ms=7,
        no_answer=False,
    )


async def _drain(generator) -> list[bytes]:
    frames: list[bytes] = []
    async for chunk in generator:
        frames.append(chunk)
    return frames


def _parse(frames: list[bytes]) -> list[dict]:
    events: list[dict] = []
    for raw in frames:
        event_name = ""
        data_str = ""
        for line in raw.decode("utf-8").splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_str = line.removeprefix("data: ")
        if event_name and data_str:
            events.append({"event": event_name, "data": json.loads(data_str)})
    return events


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rlm_emits_status_per_turn_then_final() -> None:
    dispatcher = _FakeDispatcher(is_rag=False, response=_answer_response(), turns=2)
    answer_service = AsyncMock()
    retrieval = _Retrieval(hits=[_hit("never read")])
    answer_service._retrieval = retrieval

    frames = await _drain(
        stream_answer_sse(
            dispatcher=dispatcher,
            answer_service=answer_service,
            request=_answer_request(),
            ctx=_Ctx(),
        )
    )
    events = _parse(frames)

    assert [e["event"] for e in events] == ["status", "status", "final"]
    for i, status in enumerate(events[:2], start=1):
        assert status["data"]["stage"] == "reasoning"
        assert status["data"]["turn"] == i
        assert status["data"]["accessed"] == [f"doc-{i}"]
    assert events[-1]["data"]["answer"] == "The scope is acme/ws-1."
    # RLM never touches the retrieval pipeline.
    assert retrieval.search_calls == 0


@pytest.mark.asyncio
async def test_rag_emits_hit_per_citation_then_final() -> None:
    dispatcher = _FakeDispatcher(is_rag=True, response=_answer_response())
    answer_service = AsyncMock()
    retrieval = _Retrieval(hits=[_hit("hit one"), _hit("hit two")])
    answer_service._retrieval = retrieval

    frames = await _drain(
        stream_answer_sse(
            dispatcher=dispatcher,
            answer_service=answer_service,
            request=_answer_request(),
            ctx=_Ctx(),
        )
    )
    events = _parse(frames)

    assert [e["event"] for e in events] == ["hit", "hit", "final"]
    assert events[-1]["data"]["answer"] == "The scope is acme/ws-1."
    assert retrieval.search_calls == 1


@pytest.mark.asyncio
async def test_failing_answer_emits_single_error_frame() -> None:
    answer_service = AsyncMock()
    retrieval = _Retrieval(hits=[])
    answer_service._retrieval = retrieval

    frames = await _drain(
        stream_answer_sse(
            dispatcher=_FailingDispatcher(is_rag=True),
            answer_service=answer_service,
            request=_answer_request(),
            ctx=_Ctx(),
        )
    )
    events = _parse(frames)

    assert len(events) == 1, f"expected one error frame, got {events}"
    assert events[0]["event"] == "error"
    assert events[0]["data"]["code"] == "stream_error"
    assert "answer boom" in events[0]["data"]["message"]
