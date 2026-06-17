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

"""Mode-aware SSE coverage for the query-stream endpoints.

The user-tier ``POST /api/v1/query/stream`` and agent-tier
``POST /api/v1/agent/query/stream`` are routed through the
:class:`AnswerDispatcher`. In RLM mode (the default) the stream emits a
single ``status`` (``reasoning``) frame then the terminal ``final`` frame
and **never** touches the retrieval pipeline. In the legacy RAG mode the
stream emits one ``hit`` frame per retrieved citation then the ``final``
frame, exactly as before.

All LLM / retrieval work is faked -- no network or Anthropic calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse, Hit
from flycanon.web.controllers.query_stream_controller import QueryStreamController

# ---------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------


class _StubRequest:
    """Starlette-compatible Request stub with a headers dict."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _FakeDispatcher:
    """Fake :class:`AnswerDispatcher` returning a canned response."""

    def __init__(self, *, is_rag: bool, response: AnswerResponse) -> None:
        self.is_rag = is_rag
        self.mode = "rag" if is_rag else "rlm"
        self._response = response
        self.answer_calls = 0

    async def answer(self, request, *, tenant_id=None, workspace_id=None):
        self.answer_calls += 1
        return self._response


class _RecordingRetrieval:
    """Retrieval double that records whether ``search`` was called."""

    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits
        self.search_calls = 0

    async def search(self, **kwargs):
        self.search_calls += 1
        # The controller reads ``.hits`` off the returned object.
        return self


def _user_request() -> _StubRequest:
    return _StubRequest(headers={"X-Tenant-Id": "acme", "X-Workspace-Id": "ws-1"})


def _answer_request() -> AnswerRequest:
    return AnswerRequest(question="What is the scope?", top_k=3)


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


def _parse_sse_events(frames: list[bytes]) -> list[dict]:
    events: list[dict] = []
    for raw in frames:
        text = raw.decode("utf-8")
        event_name = ""
        data_str = ""
        for line in text.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_str = line.removeprefix("data: ")
        if event_name and data_str:
            events.append({"event": event_name, "data": json.loads(data_str)})
    return events


# ---------------------------------------------------------------------
# User-tier stream -- RLM (default) mode
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_rlm_mode_emits_status_then_final_no_hits() -> None:
    response_dto = _answer_response()
    dispatcher = _FakeDispatcher(is_rag=False, response=response_dto)

    answer_service = AsyncMock()
    retrieval = _RecordingRetrieval(hits=[_hit("should never be read")])
    answer_service._retrieval = retrieval

    controller = QueryStreamController(
        queries=AsyncMock(),
        answer_service=answer_service,
        answer_dispatcher=dispatcher,
        suggester=AsyncMock(),
    )

    response = await controller.stream_answer(_user_request(), _answer_request())
    events = _parse_sse_events(await _drain(response.body_iterator))

    # status -> final, no hit frames.
    assert [e["event"] for e in events] == ["status", "final"]
    assert events[0]["data"]["stage"] == "reasoning"
    assert events[1]["data"]["answer"] == "The scope is acme/ws-1."
    assert events[1]["data"]["model"] == "rlm-root"
    assert events[1]["data"]["no_answer"] is False
    assert len(events[1]["data"]["citations"]) == 1

    # RLM must NOT touch the retrieval pipeline (no embeddings).
    assert retrieval.search_calls == 0
    assert dispatcher.answer_calls == 1


# ---------------------------------------------------------------------
# User-tier stream -- legacy RAG mode
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_rag_mode_emits_hit_then_final() -> None:
    response_dto = _answer_response()
    dispatcher = _FakeDispatcher(is_rag=True, response=response_dto)

    answer_service = AsyncMock()
    retrieval = _RecordingRetrieval(hits=[_hit("hit one"), _hit("hit two")])
    answer_service._retrieval = retrieval

    controller = QueryStreamController(
        queries=AsyncMock(),
        answer_service=answer_service,
        answer_dispatcher=dispatcher,
        suggester=AsyncMock(),
    )

    response = await controller.stream_answer(_user_request(), _answer_request())
    events = _parse_sse_events(await _drain(response.body_iterator))

    assert [e["event"] for e in events] == ["hit", "hit", "final"]
    assert events[-1]["data"]["answer"] == "The scope is acme/ws-1."
    assert retrieval.search_calls == 1
    assert dispatcher.answer_calls == 1


# ---------------------------------------------------------------------
# Agent-tier stream -- RLM (default) + RAG modes
# ---------------------------------------------------------------------


class _InMemoryAgentTokenRepository:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def insert(self, row: dict) -> None:
        self._rows[row["id"]] = row

    async def get_by_prefix(self, prefix: str, *, tenant_id: str) -> dict | None:
        for row in self._rows.values():
            if row["prefix"] == prefix and row["tenant_id"] == tenant_id and row["revoked_at"] is None:
                return row
        return None

    async def list_for_tenant(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def revoke(self, token_id: str, *, tenant_id: str, at) -> bool:
        row = self._rows.get(token_id)
        if not row or row["tenant_id"] != tenant_id or row["revoked_at"] is not None:
            return False
        row["revoked_at"] = at
        return True

    async def mark_used(self, token_id: str, *, tenant_id: str, at) -> None:
        row = self._rows.get(token_id)
        if row is not None and row["tenant_id"] == tenant_id:
            row["last_used_at"] = at


async def _mint_agent_controller(answer_service, dispatcher):
    from flycanon.core.services.auth.agent_token_service import (
        AgentTokenService,
        MintRequest,
    )
    from flycanon.web.controllers.agent.query_controller import AgentQueryController
    from flycanon.web.conventions import InMemoryIdempotencyStore

    service = AgentTokenService(_InMemoryAgentTokenRepository())
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="t",
            workspace_allowlist=None,
            scopes=["agent.query:run"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="anonymous",
    )
    controller = AgentQueryController(
        agent_token_service=service,
        queries=AsyncMock(),
        answer_service=answer_service,
        answer_dispatcher=dispatcher,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    return controller, minted.token


def _agent_request(token: str) -> _StubRequest:
    return _StubRequest(
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-1",
            "X-Agent-Token": token,
            "Idempotency-Key": "K-mode",
        }
    )


@pytest.mark.asyncio
async def test_agent_rlm_mode_emits_status_then_final_no_hits() -> None:
    dispatcher = _FakeDispatcher(is_rag=False, response=_answer_response())
    answer_service = AsyncMock()
    retrieval = _RecordingRetrieval(hits=[_hit("never read")])
    answer_service._retrieval = retrieval

    controller, token = await _mint_agent_controller(answer_service, dispatcher)

    response = await controller.stream_answer(_agent_request(token), _answer_request())
    events = _parse_sse_events(await _drain(response.body_iterator))

    assert [e["event"] for e in events] == ["status", "final"]
    assert events[0]["data"]["stage"] == "reasoning"
    assert events[1]["data"]["model"] == "rlm-root"
    assert retrieval.search_calls == 0
    assert dispatcher.answer_calls == 1


@pytest.mark.asyncio
async def test_agent_rag_mode_emits_hit_then_final() -> None:
    dispatcher = _FakeDispatcher(is_rag=True, response=_answer_response())
    answer_service = AsyncMock()
    retrieval = _RecordingRetrieval(hits=[_hit("hit one")])
    answer_service._retrieval = retrieval

    controller, token = await _mint_agent_controller(answer_service, dispatcher)

    response = await controller.stream_answer(_agent_request(token), _answer_request())
    events = _parse_sse_events(await _drain(response.body_iterator))

    assert [e["event"] for e in events] == ["hit", "final"]
    assert retrieval.search_calls == 1
    assert dispatcher.answer_calls == 1
