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

"""Unit tests for the synchronous Anthropic client.

No network: the ``httpx.Client`` is faked, and there is no ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import httpx
import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.query.rlm.client import AnthropicClient, _strip_provider, request_shape


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """Records POST bodies and replays a queued list of responses."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json, headers):  # noqa: A002 - mirror httpx signature
        self.calls.append({"url": url, "json": json, "headers": headers})
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _settings() -> CanonSettings:
    return CanonSettings()


def test_strip_provider_drops_prefix():
    assert _strip_provider("anthropic:claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert _strip_provider("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_default_models_are_stripped(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = AnthropicClient(_settings(), http_client=_FakeHttp([]))
    assert client.root_model == "claude-sonnet-4-6"
    assert client.sub_model == "claude-sonnet-4-6"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = AnthropicClient(_settings(), http_client=_FakeHttp([]))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY not set"):
        client.complete("hello")


def test_fork_isolates_tokens_but_shares_pool():
    # fork() is the concurrency-correctness primitive for per-query cost: each
    # query gets a fresh token tally while sharing the connection pool.
    http = _FakeHttp([])
    parent = AnthropicClient(_settings(), http_client=http)
    parent._record_usage("claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50})

    child = parent.fork()

    assert child is not parent
    assert child._http is parent._http  # shared httpx.Client / connection pool
    assert child.token_totals()["input_tokens"] == 0  # fresh per-query tally
    assert child.token_totals()["output_tokens"] == 0
    # the parent's accounting is untouched by the fork
    assert parent.token_totals()["input_tokens"] == 100


def test_complete_returns_joined_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {
        "content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}],
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)
    assert client.complete("q") == "Hello world"
    # the provider prefix is stripped before the id reaches the API
    assert http.calls[0]["json"]["model"] == "claude-sonnet-4-6"
    assert http.calls[0]["headers"]["x-api-key"] == "k"


def test_chat_raw_returns_full_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "tool_use", "id": "t1", "input": {}}], "stop_reason": "tool_use"}
    http = _FakeHttp([_FakeResponse(200, payload, text="")])
    client = AnthropicClient(_settings(), http_client=http)
    resp = client.chat_raw([{"role": "user", "content": "hi"}], "sys", [{"name": "python"}])
    assert resp["stop_reason"] == "tool_use"
    body = http.calls[0]["json"]
    assert body["tools"] == [{"name": "python"}]
    # prompt caching is on by default, so the system prompt is a cached text block
    assert body["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]


def test_prompt_cache_wraps_system_in_chat_raw(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "tool_use", "id": "t1", "input": {}}], "stop_reason": "tool_use"}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)  # default: prompt cache on
    client.chat_raw([{"role": "user", "content": "hi"}], "big system", [{"name": "python"}])
    assert http.calls[0]["json"]["system"] == [
        {"type": "text", "text": "big system", "cache_control": {"type": "ephemeral"}}
    ]


def test_prompt_cache_disabled_keeps_plain_system_in_chat_raw(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "tool_use", "id": "t1", "input": {}}], "stop_reason": "tool_use"}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(CanonSettings(rlm_prompt_cache=False), http_client=http)
    client.chat_raw([{"role": "user", "content": "hi"}], "big system", [{"name": "python"}])
    assert http.calls[0]["json"]["system"] == "big system"


def test_prompt_cache_wraps_system_in_complete(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)  # default: prompt cache on
    client.complete("q", system="big system")
    assert http.calls[0]["json"]["system"] == [
        {"type": "text", "text": "big system", "cache_control": {"type": "ephemeral"}}
    ]


def test_prompt_cache_disabled_keeps_plain_system_in_complete(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(CanonSettings(rlm_prompt_cache=False), http_client=http)
    client.complete("q", system="big system")
    assert http.calls[0]["json"]["system"] == "big system"


def test_complete_without_system_omits_field(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)
    client.complete("q")
    assert "system" not in http.calls[0]["json"]


def test_model_override_is_stripped(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)
    client.complete("q", model="anthropic:claude-haiku-4-5")
    assert http.calls[0]["json"]["model"] == "claude-haiku-4-5"


def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr("flycanon.core.services.query.rlm.client.time.sleep", lambda _s: None)
    ok = {"content": [{"type": "text", "text": "done"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(429, text="slow down"), _FakeResponse(200, ok)])
    client = AnthropicClient(_settings(), http_client=http)
    assert client.complete("q") == "done"
    assert len(http.calls) == 2


def test_non_retriable_status_breaks_immediately(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr("flycanon.core.services.query.rlm.client.time.sleep", lambda _s: None)
    http = _FakeHttp([_FakeResponse(400, text="bad request")])
    client = AnthropicClient(_settings(), http_client=http)
    with pytest.raises(RuntimeError, match="400"):
        client.complete("q")
    assert len(http.calls) == 1


def test_retry_on_httpx_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr("flycanon.core.services.query.rlm.client.time.sleep", lambda _s: None)
    ok = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([httpx.ConnectError("boom"), _FakeResponse(200, ok)])
    client = AnthropicClient(_settings(), http_client=http)
    assert client.complete("q") == "ok"


def test_token_accounting_sums_and_costs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    }
    http = _FakeHttp([_FakeResponse(200, payload), _FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)
    client.complete("a")
    client.complete("b")
    totals = client.token_totals()
    assert totals["input_tokens"] == 2_000_000
    assert totals["output_tokens"] == 2_000_000
    # 2M in @ $3 + 2M out @ $15 for claude-sonnet-4-6
    assert totals["estimated_cost_usd"] == pytest.approx(2 * 3.0 + 2 * 15.0)
    assert totals["by_model"]["claude-sonnet-4-6"]["input"] == 2_000_000


def test_reset_tokens_clears(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 5, "output_tokens": 2}}
    client = AnthropicClient(_settings(), http_client=_FakeHttp([_FakeResponse(200, payload)]))
    client.complete("a")
    client.reset_tokens()
    totals = client.token_totals()
    assert totals == {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "by_model": {}}


# ---------------------------------------------------------------------------
# Request shape per model generation. Recorded through the fake transport, so
# what is asserted is the wire body the Anthropic API would receive.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "adaptive"),
    [
        ("anthropic:claude-sonnet-5", True),
        ("claude-opus-5", True),
        ("claude-fable-5-1", True),
        ("anthropic:claude-sonnet-4-6", True),
        ("claude-opus-4-6", True),
        ("claude-opus-4-7", True),
        ("claude-opus-4-8", True),
        ("claude-haiku-4-5", False),
        ("claude-sonnet-4-5", False),
        ("claude-haiku-4-5-20251001", False),  # dated snapshot ids keep their generation
        ("claude-3-5-sonnet-20241022", False),  # the pre-4 naming does not parse: legacy
        ("gpt-4o", False),  # not Claude at all: the request is what it always was
    ],
)
def test_request_shape_classifies_the_model_generation(model: str, adaptive: bool):
    shape = request_shape(model)
    assert shape.adaptive is adaptive
    assert ":" not in shape.model


def test_adaptive_model_sends_thinking_and_no_sampling_knobs_on_chat_raw(monkeypatch):
    """claude-sonnet-5: ``thinking: adaptive``, no temperature/top_p/top_k, floored max_tokens.

    The recorded body is the request the dworkers stack measured a 400
    against (``temperature is deprecated for this model``) before 26.7.1.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "tool_use", "id": "t1", "input": {}}], "stop_reason": "tool_use"}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(
        CanonSettings(rlm_root_model="anthropic:claude-sonnet-5", rlm_sub_model="anthropic:claude-sonnet-5"),
        http_client=http,
    )
    client.chat_raw([{"role": "user", "content": "hi"}], "sys", [{"name": "python"}])
    body = http.calls[0]["json"]
    assert body["model"] == "claude-sonnet-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert "temperature" not in body and "top_p" not in body and "top_k" not in body
    # 1500 (the tool-turn default) is raised to the adaptive floor: thinking
    # tokens count against max_tokens and a mid-thought cut-off reads as an
    # empty answer to the session.
    assert body["max_tokens"] == 8192
    assert body["tools"] == [{"name": "python"}]
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert set(body) == {"model", "max_tokens", "thinking", "system", "messages", "tools"}


def test_adaptive_model_sends_thinking_and_no_sampling_knobs_on_complete(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "seventy euros"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)
    assert client.complete("q", model="anthropic:claude-opus-5", max_tokens=16000) == "seventy euros"
    body = http.calls[0]["json"]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert "temperature" not in body
    assert body["max_tokens"] == 16000  # a caller's budget above the floor is kept as is
    assert set(body) == {"model", "max_tokens", "thinking", "messages"}


def test_legacy_model_keeps_the_deterministic_temperature(monkeypatch):
    """Haiku 4.5 has no adaptive mode: the body is byte-for-byte the pre-26.7.1 one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload), _FakeResponse(200, payload)])
    client = AnthropicClient(
        CanonSettings(
            rlm_root_model="anthropic:claude-haiku-4-5", rlm_sub_model="anthropic:claude-haiku-4-5"
        ),
        http_client=http,
    )
    client.complete("q")
    client.chat_raw([{"role": "user", "content": "hi"}], "sys", [])
    for call in http.calls:
        body = call["json"]
        assert body["model"] == "claude-haiku-4-5"
        assert body["temperature"] == 0.0
        assert "thinking" not in body
    assert http.calls[0]["json"]["max_tokens"] == 1000  # complete() default, not floored
    assert http.calls[1]["json"]["max_tokens"] == 1500  # chat_raw() default, not floored


def test_default_sonnet_4_6_is_on_the_adaptive_shape(monkeypatch):
    """The settings default (claude-sonnet-4-6) is a 4.6 model: adaptive, no temperature."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
    http = _FakeHttp([_FakeResponse(200, payload)])
    AnthropicClient(_settings(), http_client=http).complete("q")
    body = http.calls[0]["json"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["thinking"] == {"type": "adaptive"} and "temperature" not in body


def test_token_accounting_prices_the_5_series(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {
        "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    }
    http = _FakeHttp([_FakeResponse(200, payload), _FakeResponse(200, payload)])
    client = AnthropicClient(_settings(), http_client=http)
    client.complete("a", model="anthropic:claude-sonnet-5")
    client.complete("b", model="anthropic:claude-opus-4-8")
    totals = client.token_totals()
    # 1M in @ $2 + 1M out @ $10 (Sonnet 5) + 1M in @ $5 + 1M out @ $25 (Opus 4.8)
    assert totals["estimated_cost_usd"] == pytest.approx(2.0 + 10.0 + 5.0 + 25.0)
    assert set(totals["by_model"]) == {"claude-sonnet-5", "claude-opus-4-8"}
