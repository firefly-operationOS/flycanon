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
from flycanon.core.services.query.rlm.client import AnthropicClient, _strip_provider


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
