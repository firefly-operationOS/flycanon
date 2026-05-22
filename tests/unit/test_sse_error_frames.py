# Copyright 2026 Firefly Software Solutions Inc
"""SSE error-frame coverage for the query-stream endpoints.

Both the user-tier ``POST /api/v1/query/stream`` and the agent-tier
``POST /api/v1/agent/query/stream`` wrap their generators in
``try/except Exception``. A mid-stream failure used to leave the SSE
socket open with no terminal frame; the client hung until the idle
timeout fired. Now: the failure is logged + emitted as a single
``event: error`` frame, then the stream closes cleanly.

The shape of the error frame is fixed (``{"code": "stream_error",
"message": <stringified exc>}``) so consuming agents can branch on it
without parsing the human-readable detail.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from flycanon.web.controllers.query_stream_controller import QueryStreamController

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


class _StubRequest:
    """Starlette-compatible Request stub with a headers dict."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _user_request() -> _StubRequest:
    return _StubRequest(
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-1",
        }
    )


def _answer_request():
    from flycanon.interfaces.dtos.query import AnswerRequest

    return AnswerRequest(question="What is the scope?", top_k=3)


async def _drain(generator) -> list[bytes]:
    """Materialise the async generator into a list of emitted frames."""
    frames: list[bytes] = []
    async for chunk in generator:
        frames.append(chunk)
    return frames


def _parse_sse_events(frames: list[bytes]) -> list[dict]:
    """Parse raw SSE frames into ``[{"event": str, "data": dict}, ...]``."""
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
# User-tier stream
# ---------------------------------------------------------------------


class TestUserQueryStreamErrorFrame:
    @pytest.mark.asyncio
    async def test_mid_stream_exception_emits_error_frame(self) -> None:
        # The retrieval call raises mid-stream; the SSE generator must
        # surface exactly one ``error`` frame and close cleanly.
        answer_service = AsyncMock()
        retrieval = AsyncMock()
        retrieval.search = AsyncMock(side_effect=RuntimeError("retrieval boom"))
        answer_service._retrieval = retrieval

        controller = QueryStreamController(
            queries=AsyncMock(),
            answer_service=answer_service,
            suggester=AsyncMock(),
        )

        response = await controller.stream_answer(_user_request(), _answer_request())
        frames = await _drain(response.body_iterator)
        events = _parse_sse_events(frames)

        # Exactly one event, and it's the error frame with the canonical code.
        assert len(events) == 1, f"expected one error frame, got {events}"
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "stream_error"
        assert "retrieval boom" in events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_answer_phase_failure_emits_error_frame(self) -> None:
        # Retrieval succeeds (no hits), then the answer call raises.
        # The "hit" phase emits nothing because hits is empty -- the
        # error frame must still be the only emitted frame.
        answer_service = AsyncMock()
        retrieval = AsyncMock()
        retrieval.hits = []
        retrieval.search = AsyncMock(return_value=retrieval)
        answer_service._retrieval = retrieval
        answer_service.answer = AsyncMock(side_effect=RuntimeError("answer boom"))

        controller = QueryStreamController(
            queries=AsyncMock(),
            answer_service=answer_service,
            suggester=AsyncMock(),
        )

        response = await controller.stream_answer(_user_request(), _answer_request())
        frames = await _drain(response.body_iterator)
        events = _parse_sse_events(frames)

        assert len(events) == 1
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "stream_error"
        assert "answer boom" in events[0]["data"]["message"]


# ---------------------------------------------------------------------
# Agent-tier stream
# ---------------------------------------------------------------------


class _InMemoryAgentTokenRepository:
    """Minimal mirror of the production repository."""

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


class TestAgentQueryStreamErrorFrame:
    @pytest.mark.asyncio
    async def test_mid_stream_exception_emits_error_frame(self) -> None:
        from flycanon.core.services.auth.agent_token_service import (
            AgentTokenService,
            MintRequest,
        )
        from flycanon.web.controllers.agent.query_controller import AgentQueryController

        repo = _InMemoryAgentTokenRepository()
        service = AgentTokenService(repo)
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
        retrieval = AsyncMock()
        retrieval.search = AsyncMock(side_effect=RuntimeError("retrieval boom"))
        answer_service._retrieval = retrieval

        from flycanon.web.conventions import InMemoryIdempotencyStore

        controller = AgentQueryController(
            agent_token_service=service,
            queries=AsyncMock(),
            answer_service=answer_service,
            idempotency_store=InMemoryIdempotencyStore(),
        )

        agent_request = _StubRequest(
            headers={
                "X-Tenant-Id": "acme",
                "X-Workspace-Id": "ws-1",
                "X-Agent-Token": minted.token,
                "Idempotency-Key": "K-stream-err",
            }
        )

        response = await controller.stream_answer(agent_request, _answer_request())
        frames = await _drain(response.body_iterator)
        events = _parse_sse_events(frames)

        assert len(events) == 1, f"expected one error frame, got {events}"
        assert events[0]["event"] == "error"
        assert events[0]["data"]["code"] == "stream_error"
        assert "retrieval boom" in events[0]["data"]["message"]
