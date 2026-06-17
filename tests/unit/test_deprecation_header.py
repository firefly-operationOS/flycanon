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

"""``X-Flycanon-Deprecation`` header coverage for the answer endpoints.

When ``FLYCANON_ANSWER_MODE=rag`` the deprecated RAG engine is active and
every answer surface -- the non-streaming ``POST /api/v1/query``, the SSE
``POST /api/v1/query/stream``, and the agent-tier equivalents -- attaches
an ``X-Flycanon-Deprecation`` response header announcing the deprecation.
In RLM mode (the default) no such header is set. The header is a pure
signal: the response body is byte-for-byte the same ``AnswerResponse``.

All LLM / retrieval work is faked -- no network or Anthropic calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flycanon.core.services.auth.agent_token_service import AgentTokenService, MintRequest
from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse, Hit
from flycanon.web.controllers.agent.query_controller import AgentQueryController
from flycanon.web.controllers.query_controller import QueryController
from flycanon.web.controllers.query_stream_controller import QueryStreamController
from flycanon.web.conventions import InMemoryIdempotencyStore
from flycanon.web.conventions.headers import DEPRECATION_RAG_MESSAGE, HEADER_DEPRECATION

# ---------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------


class _StubRequest:
    """Starlette-compatible Request stub with a headers dict."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _FakeDispatcher:
    """Fake :class:`AnswerDispatcher` exposing the read-only ``is_rag`` flag."""

    def __init__(self, *, is_rag: bool, response: AnswerResponse) -> None:
        self.is_rag = is_rag
        self.mode = "rag" if is_rag else "rlm"
        self._response = response

    async def answer(self, request, *, tenant_id=None, workspace_id=None):
        return self._response


class _RecordingRetrieval:
    """Retrieval double the streaming controllers read ``.hits`` off of."""

    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits

    async def search(self, **kwargs):
        return self


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


def _user_request() -> _StubRequest:
    return _StubRequest(headers={"X-Tenant-Id": "acme", "X-Workspace-Id": "ws-1"})


def _agent_request(token: str) -> _StubRequest:
    return _StubRequest(
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-1",
            "X-Agent-Token": token,
            "Idempotency-Key": "K-dep",
        }
    )


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


async def _mint_agent_controller(dispatcher) -> tuple[AgentQueryController, str]:
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
    answer_service = AsyncMock()
    answer_service._retrieval = _RecordingRetrieval(hits=[_hit("hit one")])
    controller = AgentQueryController(
        agent_token_service=service,
        queries=AsyncMock(query=AsyncMock(return_value=_answer_response())),
        answer_service=answer_service,
        answer_dispatcher=dispatcher,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    return controller, minted.token


async def _drain(generator) -> None:
    async for _ in generator:
        pass


# ---------------------------------------------------------------------
# User-tier non-streaming POST /api/v1/query
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_query_rag_sets_deprecation_header() -> None:
    response_dto = _answer_response()
    queries = AsyncMock(query=AsyncMock(return_value=response_dto))
    dispatcher = _FakeDispatcher(is_rag=True, response=response_dto)
    controller = QueryController(queries=queries, answer_dispatcher=dispatcher)

    result = await controller.answer(_user_request(), _answer_request())

    assert result.headers[HEADER_DEPRECATION] == DEPRECATION_RAG_MESSAGE


@pytest.mark.asyncio
async def test_user_query_rlm_omits_deprecation_header() -> None:
    response_dto = _answer_response()
    queries = AsyncMock(query=AsyncMock(return_value=response_dto))
    dispatcher = _FakeDispatcher(is_rag=False, response=response_dto)
    controller = QueryController(queries=queries, answer_dispatcher=dispatcher)

    result = await controller.answer(_user_request(), _answer_request())

    # RLM returns the DTO untouched -- no Response wrapper, no header.
    assert result is response_dto
    assert not hasattr(result, "headers")


# ---------------------------------------------------------------------
# User-tier SSE POST /api/v1/query/stream
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_stream_rag_sets_deprecation_header() -> None:
    response_dto = _answer_response()
    dispatcher = _FakeDispatcher(is_rag=True, response=response_dto)
    answer_service = AsyncMock()
    answer_service._retrieval = _RecordingRetrieval(hits=[_hit("hit one")])
    controller = QueryStreamController(
        queries=AsyncMock(),
        answer_service=answer_service,
        answer_dispatcher=dispatcher,
        suggester=AsyncMock(),
    )

    response = await controller.stream_answer(_user_request(), _answer_request())

    assert response.headers[HEADER_DEPRECATION] == DEPRECATION_RAG_MESSAGE
    await _drain(response.body_iterator)


@pytest.mark.asyncio
async def test_user_stream_rlm_omits_deprecation_header() -> None:
    response_dto = _answer_response()
    dispatcher = _FakeDispatcher(is_rag=False, response=response_dto)
    answer_service = AsyncMock()
    answer_service._retrieval = _RecordingRetrieval(hits=[_hit("never read")])
    controller = QueryStreamController(
        queries=AsyncMock(),
        answer_service=answer_service,
        answer_dispatcher=dispatcher,
        suggester=AsyncMock(),
    )

    response = await controller.stream_answer(_user_request(), _answer_request())

    assert HEADER_DEPRECATION not in response.headers
    await _drain(response.body_iterator)


# ---------------------------------------------------------------------
# Agent-tier non-streaming POST /api/v1/agent/query
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_query_rag_sets_deprecation_header() -> None:
    dispatcher = _FakeDispatcher(is_rag=True, response=_answer_response())
    controller, token = await _mint_agent_controller(dispatcher)

    result = await controller.answer(_agent_request(token), _answer_request())

    assert result.headers[HEADER_DEPRECATION] == DEPRECATION_RAG_MESSAGE


@pytest.mark.asyncio
async def test_agent_query_rlm_omits_deprecation_header() -> None:
    response_dto = _answer_response()
    dispatcher = _FakeDispatcher(is_rag=False, response=response_dto)
    controller, token = await _mint_agent_controller(dispatcher)

    result = await controller.answer(_agent_request(token), _answer_request())

    # RLM returns the DTO untouched -- no Response wrapper, no header.
    assert isinstance(result, AnswerResponse)
    assert not hasattr(result, "headers")


# ---------------------------------------------------------------------
# Agent-tier SSE POST /api/v1/agent/query/stream
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_stream_rag_sets_deprecation_header() -> None:
    dispatcher = _FakeDispatcher(is_rag=True, response=_answer_response())
    controller, token = await _mint_agent_controller(dispatcher)

    response = await controller.stream_answer(_agent_request(token), _answer_request())

    assert response.headers[HEADER_DEPRECATION] == DEPRECATION_RAG_MESSAGE
    await _drain(response.body_iterator)


@pytest.mark.asyncio
async def test_agent_stream_rlm_omits_deprecation_header() -> None:
    dispatcher = _FakeDispatcher(is_rag=False, response=_answer_response())
    controller, token = await _mint_agent_controller(dispatcher)

    response = await controller.stream_answer(_agent_request(token), _answer_request())

    assert HEADER_DEPRECATION not in response.headers
    await _drain(response.body_iterator)
