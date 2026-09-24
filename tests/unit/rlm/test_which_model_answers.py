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

"""Which model produces the RLM's final answer -- pinned by recorded requests.

26.7.0 shipped three RLM model settings and documented the third,
``FLYCANON_RLM_ANSWER_MODEL``, as "the model for the final single-shot
answer synthesis". Nothing read it. Worse, the docs that described it
(and the dworkers runbook that copied them) stated that the synthesis ran
on the SUB model. Neither was true: the final answer is the ROOT model's
in both ways a session can end -- the ``final(...)`` tool call of the
CodeAct loop and the tool-less forced-final turn after ``max_iters`` are
both ``chat_raw`` turns, and ``chat_raw`` resolves ``model=None`` to
``root_model``. The sub model serves only the ``llm()`` / ``rlm()``
helpers (``complete``) and the self-consistency selector.

These tests drive a whole :class:`RLMSession` through the REAL
:class:`AnthropicClient` over a recording HTTP fake, with the root and sub
models deliberately different, and assert from the recorded request
bodies which model each turn went to. They are the evidence behind the
26.7.1 CHANGELOG line that removed the third setting, and they fail the
day someone re-routes the forced-final turn without updating the docs.
"""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.query.rlm.client import AnthropicClient
from flycanon.core.services.query.rlm.session import RLMSession

ROOT = "anthropic:claude-opus-5"
SUB = "anthropic:claude-haiku-4-5"


class _FakeResponse:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return self._payload


class _RecordingHttp:
    """Replays queued Messages responses and keeps every request body, in order."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.bodies: list[dict] = []

    def post(self, url, json, headers):  # noqa: A002 - mirror httpx signature
        self.bodies.append(json)
        return _FakeResponse(self._responses.pop(0))


class _Docs:
    """The smallest DocCorpus: one filing, one page."""

    def __init__(self, page: str):
        self._page = page

    def keys(self):
        return ["POLICY"]

    def __getitem__(self, key: str) -> str:
        return self._page

    def __contains__(self, key: object) -> bool:
        return key == "POLICY"

    def pages(self, key: str) -> list[str]:
        return [self._page]

    def npages(self, key: str) -> int:
        return 1


def _tool_use(code: str, tool_id: str) -> dict:
    return {
        "content": [{"type": "tool_use", "id": tool_id, "name": "python", "input": {"code": code}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _text(text: str) -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _client(monkeypatch, responses: list[dict]) -> tuple[AnthropicClient, _RecordingHttp]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    http = _RecordingHttp(responses)
    client = AnthropicClient(CanonSettings(rlm_root_model=ROOT, rlm_sub_model=SUB), http_client=http)
    return client, http


def test_the_final_tool_call_is_answered_by_the_root_model(monkeypatch):
    """The normal ending: ``final(...)`` is a tool call of the root loop."""
    client, http = _client(monkeypatch, [_tool_use("final('70 euros', filings=['POLICY'], pages=[0])", "t1")])
    answer, cites, no_answer = RLMSession(client).run("meal allowance?", _Docs("70 euros a day"))
    assert (answer, no_answer) == ("70 euros", False)
    assert cites[0]["filing"] == "POLICY"
    assert [b["model"] for b in http.bodies] == ["claude-opus-5"]
    assert http.bodies[0]["tools"][0]["name"] == "python"


def test_the_forced_final_turn_is_answered_by_the_root_model(monkeypatch):
    """The safety net: out of turns, a tool-less ``chat_raw`` -- still the root model.

    This is the turn 26.7.0's docs called "the final single-shot answer
    synthesis" and attributed to a third model; the recorded body says
    which model really receives it.
    """
    client, http = _client(
        monkeypatch,
        [
            _tool_use("print('still reading')", "t1"),
            _tool_use("print('still reading')", "t2"),
            _text("The allowance is 70 euros a day."),
        ],
    )
    answer, _cites, no_answer = RLMSession(client, max_iters=2).run("meal allowance?", _Docs("70"))
    assert answer == "The allowance is 70 euros a day."
    assert no_answer is False
    assert [b["model"] for b in http.bodies] == ["claude-opus-5"] * 3
    forced = http.bodies[-1]
    assert forced["tools"] == []
    assert forced["max_tokens"] >= 4096
    # opus-5 is adaptive: the forced-final turn carries the same request shape
    # as every other root turn, no sampling knobs slipped in on this path.
    assert forced["thinking"] == {"type": "adaptive"} and "temperature" not in forced


def test_only_the_repl_sub_calls_go_to_the_sub_model(monkeypatch):
    """``llm()`` from REPL code is the ONE thing the sub model answers in a session."""
    client, http = _client(
        monkeypatch,
        [
            _tool_use("print(llm('extract the number'))", "t1"),
            _text("extracted: 70"),  # the llm() sub-call
            _tool_use("final('70', filings=['POLICY'])", "t2"),
        ],
    )
    answer, _cites, _no_answer = RLMSession(client).run("q", _Docs("70"))
    assert answer == "70"
    assert [b["model"] for b in http.bodies] == ["claude-opus-5", "claude-haiku-4-5", "claude-opus-5"]
    sub_call = http.bodies[1]
    assert "tools" not in sub_call and sub_call["messages"][0]["content"] == "extract the number"
    # haiku-4-5 is legacy: the sub-call keeps the deterministic temperature
    # while the root turns around it are adaptive -- each turn is shaped by
    # the model it goes to, not by the session.
    assert sub_call["temperature"] == 0.0 and "thinking" not in sub_call
    assert all(b["thinking"] == {"type": "adaptive"} for b in (http.bodies[0], http.bodies[2]))


def test_no_third_model_setting_exists_and_a_stale_env_var_is_ignored(monkeypatch):
    """``FLYCANON_RLM_ANSWER_MODEL`` is gone; an env file that still sets it boots."""
    monkeypatch.setenv("FLYCANON_RLM_ANSWER_MODEL", "anthropic:claude-sonnet-4-5")
    settings = CanonSettings(rlm_root_model=ROOT, rlm_sub_model=SUB)
    assert not hasattr(settings, "rlm_answer_model")
    assert "rlm_answer_model" not in CanonSettings.model_fields
    # and the stale value cannot leak into a request through any other field
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    http = _RecordingHttp([_tool_use("final('x', filings=['POLICY'])", "t1")])
    RLMSession(AnthropicClient(settings, http_client=http)).run("q", _Docs("x"))
    assert "claude-sonnet-4-5" not in str(http.bodies)


@pytest.mark.parametrize("ending", ["final", "forced"])
def test_the_cost_row_names_the_model_that_answered(monkeypatch, ending: str):
    """Token accounting buckets by the model on the wire: root turns land on the root model."""
    responses = (
        [_tool_use("final('x', filings=['POLICY'])", "t1")]
        if ending == "final"
        else [_tool_use("print(1)", "t1"), _text("x")]
    )
    client, _http = _client(monkeypatch, responses)
    RLMSession(client, max_iters=1).run("q", _Docs("x"))
    per_model = client.token_totals()["by_model"]
    assert set(per_model) == {"claude-opus-5"}
