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

"""Unit tests for :class:`AnswerDispatcher`.

Both services are faked (no retrieval, no LLM, no network) so the tests
assert only the routing decision, the deprecation warning, and verbatim
argument forwarding -- never the underlying answer logic.
"""

from __future__ import annotations

import logging

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.query.answer_dispatcher import AnswerDispatcher
from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse


class _FakeAnswerer:
    """Records the single ``answer()`` call it received."""

    def __init__(self, label: str) -> None:
        self._label = label
        self.calls: list[dict] = []

    async def answer(
        self,
        request: AnswerRequest,
        *,
        prior_turns=None,
        tenant_id=None,
        workspace_id=None,
    ) -> AnswerResponse:
        self.calls.append(
            {
                "request": request,
                "prior_turns": prior_turns,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )
        return AnswerResponse(
            answer=self._label,
            citations=[],
            model=self._label,
            elapsed_ms=0,
            no_answer=False,
        )


def _dispatcher(mode: str) -> tuple[AnswerDispatcher, _FakeAnswerer, _FakeAnswerer]:
    rag = _FakeAnswerer("rag")
    rlm = _FakeAnswerer("rlm")
    settings = CanonSettings(answer_mode=mode)
    return AnswerDispatcher(rag=rag, rlm=rlm, settings=settings), rag, rlm


def _request() -> AnswerRequest:
    return AnswerRequest(question="What is the revenue?")


@pytest.mark.asyncio
async def test_default_routes_to_rlm():
    dispatcher, rag, rlm = _dispatcher("rlm")

    resp = await dispatcher.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.answer == "rlm"
    assert len(rlm.calls) == 1
    assert rag.calls == []
    assert dispatcher.mode == "rlm"
    assert dispatcher.is_rag is False


@pytest.mark.asyncio
async def test_rag_mode_routes_to_rag_and_warns(caplog):
    dispatcher, rag, rlm = _dispatcher("rag")

    with caplog.at_level(logging.WARNING):
        resp = await dispatcher.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.answer == "rag"
    assert len(rag.calls) == 1
    assert rlm.calls == []
    assert dispatcher.mode == "rag"
    assert dispatcher.is_rag is True
    assert any("deprecated" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_args_forwarded_verbatim():
    dispatcher, _rag, rlm = _dispatcher("rlm")
    request = _request()
    prior = [("hi", "hello")]

    await dispatcher.answer(
        request,
        prior_turns=prior,
        tenant_id="tenant-x",
        workspace_id="ws-y",
    )

    call = rlm.calls[0]
    assert call["request"] is request
    assert call["prior_turns"] is prior
    assert call["tenant_id"] == "tenant-x"
    assert call["workspace_id"] == "ws-y"


def test_unknown_mode_falls_back_to_rlm():
    dispatcher, _rag, _rlm = _dispatcher("nonsense")

    assert dispatcher.mode == "rlm"
    assert dispatcher.is_rag is False


def test_mode_normalised_to_lowercase():
    dispatcher, _rag, _rlm = _dispatcher("RAG")

    assert dispatcher.mode == "rag"
    assert dispatcher.is_rag is True
