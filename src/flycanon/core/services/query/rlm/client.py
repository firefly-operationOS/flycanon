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

"""Synchronous Anthropic Messages client for the RLM engine.

RLM (Recursive Language Models) is built entirely on LM calls: a root
orchestrator that writes code, and recursive sub-calls it makes from inside
that code. This is the thin HTTP wrapper they all share -- multi-turn
:func:`AnthropicClient.chat_raw` (the orchestrator's native tool-use loop) and
single-shot :func:`AnthropicClient.complete` (a recursive sub-call), both over
the Anthropic API.

The client is deliberately **synchronous** (``httpx.Client``): the engine is
designed to be run inside ``asyncio.to_thread`` by a later async answer
service, so blocking I/O here is correct. The API key is read from the
``ANTHROPIC_API_KEY`` environment variable; default models come from
:class:`CanonSettings`. Model ids in settings use the provider-prefixed
``anthropic:claude-sonnet-4-6`` form -- the ``anthropic:`` prefix is stripped
before the id is sent to the Anthropic API.
"""

from __future__ import annotations

import os
import threading
import time

import httpx

from flycanon.config import CanonSettings

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Per-million-token (input, output) USD prices, keyed by the bare Anthropic
# model id (no provider prefix). Unknown models contribute zero cost.
_PRICE_PER_M: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (0.25, 1.25),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-8": (15.00, 75.00),
}


def _strip_provider(model: str) -> str:
    """Drop the ``anthropic:`` (or any ``provider:``) prefix the settings use."""
    return model.split(":", 1)[1] if ":" in model else model


class AnthropicClient:
    """Thin, synchronous Anthropic Messages wrapper with token accounting.

    One instance carries its own ``httpx.Client``, token tallies, and the
    default models resolved from settings. Token accounting is process-safe
    within the instance via a lock so the orchestrator's threaded sub-calls
    don't race on the counters.
    """

    def __init__(self, settings: CanonSettings, http_client: httpx.Client | None = None):
        self._settings = settings
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._http = http_client or httpx.Client(timeout=180.0)
        self.root_model = _strip_provider(settings.rlm_root_model)
        self.sub_model = _strip_provider(settings.rlm_sub_model)
        self._prompt_cache = settings.rlm_prompt_cache
        self._tokens: dict[str, dict[str, int]] = {}
        self._token_lock = threading.Lock()

    def _system(self, system: str):
        """Build the ``system`` field, with a cache breakpoint when enabled.

        The RLM system prompt is large and identical across every Messages
        call one session makes. Sending it as a single text block tagged with
        ``cache_control: ephemeral`` lets Anthropic cache it server-side and
        bill the repeats as cache reads. When prompt caching is disabled the
        plain string is sent (current behaviour).
        """
        if self._prompt_cache:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return system

    # -- token accounting ----------------------------------------------
    def _record_usage(self, model: str, usage: dict) -> None:
        with self._token_lock:
            bucket = self._tokens.setdefault(model, {"input": 0, "output": 0})
            bucket["input"] += usage.get("input_tokens", 0)
            bucket["output"] += usage.get("output_tokens", 0)

    def reset_tokens(self) -> None:
        with self._token_lock:
            self._tokens.clear()

    def token_totals(self) -> dict:
        with self._token_lock:
            snapshot = {m: dict(v) for m, v in self._tokens.items()}
        total_in = sum(v["input"] for v in snapshot.values())
        total_out = sum(v["output"] for v in snapshot.values())
        cost = sum(
            v["input"] / 1e6 * _PRICE_PER_M.get(m, (0, 0))[0]
            + v["output"] / 1e6 * _PRICE_PER_M.get(m, (0, 0))[1]
            for m, v in snapshot.items()
        )
        return {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "estimated_cost_usd": round(cost, 4),
            "by_model": snapshot,
        }

    # -- HTTP ----------------------------------------------------------
    def _request(self, body: dict) -> dict:
        """POST with retry/backoff on 429/5xx; return the parsed response JSON."""
        if not self._api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        last = ""
        for attempt in range(6):
            try:
                r = self._http.post(API_URL, json=body, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    self._record_usage(body.get("model", "unknown"), data.get("usage", {}))
                    return data
                last = f"{r.status_code}: {r.text[:200]}"
                if r.status_code not in (429, 500, 502, 503, 529):
                    break
            except httpx.HTTPError as exc:  # noqa: PERF203
                last = str(exc)
            time.sleep(min(2**attempt, 30))
        raise RuntimeError(f"anthropic call failed: {last}")

    def _text(self, body: dict) -> str:
        content = self._request(body).get("content") or []
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        return "".join(texts)

    # -- public surface ------------------------------------------------
    def chat_raw(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str | None = None,
        max_tokens: int = 1500,
    ) -> dict:
        """Tool-enabled turn -- returns the full response (content + stop_reason).

        The RLM orchestrator drives a ``python`` tool: Claude emits native
        tool-use blocks (not markdown code fences), so native tool calling is
        far more reliable than parsing ```python out of free text.
        """
        body = {
            "model": _strip_provider(model) if model else self.root_model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "system": self._system(system),
            "messages": messages,
            "tools": tools,
        }
        return self._request(body)

    def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """Single-shot completion -- a recursive sub-call on a chunk of context."""
        body = {
            "model": _strip_provider(model) if model else self.sub_model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = self._system(system)
        return self._text(body)
