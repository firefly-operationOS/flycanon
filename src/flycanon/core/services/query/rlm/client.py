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

Two generations of Claude, two request shapes
---------------------------------------------

Claude 4.6 and everything after it (Sonnet 4.6, Opus 4.6/4.7/4.8, the whole
``claude-*-5`` line) reason with **adaptive thinking**: ``thinking: {"type":
"adaptive"}`` lets the model decide when and how much to think, and the
sampling knobs are gone with it -- ``temperature`` / ``top_p`` / ``top_k``
are refused outright on the 5-series and on Opus 4.7/4.8 (``400 temperature
is deprecated for this model``, measured by the dworkers programme on
2026-09-17 against ``claude-sonnet-5``) and are meaningless next to thinking
on 4.6. Earlier models (Haiku 4.5, Sonnet 4.5, ...) have no adaptive mode and
take ``temperature`` as before. :func:`request_shape` decides per model id,
so an operator can move ``FLYCANON_RLM_*_MODEL`` between the two generations
without touching code, and :meth:`AnthropicClient.chat_raw` /
:meth:`AnthropicClient.complete` build the body from it.

``temperature: 0.0`` on the legacy shape is kept for the same reason it was
chosen: the orchestrator writes code, and a deterministic sampler makes a
CodeAct loop reproducible run to run. Adaptive thinking gives up that knob
and gets a model that plans the code before writing it, which is the better
trade for a REPL driver.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass

import httpx

from flycanon.config import CanonSettings

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Per-million-token (input, output) USD prices, keyed by the bare Anthropic
# model id (no provider prefix). Unknown models contribute zero cost. The
# rows are the Anthropic first-party API rates; Opus 4.8 was listed at the
# old Opus tier (15/75) until 26.7.1 -- the 4.6+ Opus line is priced 5/25.
_PRICE_PER_M: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (0.25, 1.25),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
}

# ``claude-<family>-<major>[-<minor>]`` with an optional date suffix
# (``claude-sonnet-4-6``, ``claude-opus-5``, ``claude-haiku-4-5-20251001``).
_CLAUDE_ID = re.compile(r"^claude-(?P<family>[a-z]+)-(?P<major>\d+)(?:-(?P<minor>\d+))?(?:-\d{8})?$")

# An adaptive-thinking turn spends output tokens on the thinking before the
# visible answer, and the Messages API counts both against ``max_tokens``. The
# orchestrator's historical budget (1500 for a tool turn) was sized for the
# visible code alone; under adaptive thinking it would be exhausted mid-thought
# and the turn would come back with ``stop_reason: max_tokens`` and no
# ``tool_use`` block, which the session reads as "the model answered in plain
# text" -- an empty answer for a budgeting reason. Callers still choose the
# budget; on adaptive models it is raised to at least this floor. The ceiling
# is what the model MAY use, not what it is billed for, so the floor costs
# nothing on a turn that does not need it.
_ADAPTIVE_MIN_MAX_TOKENS = 8192


@dataclass(frozen=True, slots=True)
class RequestShape:
    """How the Messages API wants to be called for one model id."""

    model: str
    adaptive: bool

    def body_for(self, *, max_tokens: int) -> dict:
        """The model-dependent part of a request body.

        Adaptive models get ``thinking: adaptive`` and NO sampling knobs (the
        API refuses them); legacy models get the deterministic
        ``temperature: 0.0`` the engine has always used.
        """
        if self.adaptive:
            return {
                "model": self.model,
                "max_tokens": max(max_tokens, _ADAPTIVE_MIN_MAX_TOKENS),
                "thinking": {"type": "adaptive"},
            }
        return {"model": self.model, "max_tokens": max_tokens, "temperature": 0.0}


def request_shape(model: str) -> RequestShape:
    """Classify a (possibly provider-prefixed) model id into a :class:`RequestShape`.

    Adaptive = Claude 4.6 or later: major ≥ 5, or major 4 with minor ≥ 6.
    A ``claude-`` id that does not parse, and any non-Claude id, is treated
    as legacy so the request is exactly what it was before 26.7.1 -- the
    conservative side for an unknown model on a raw HTTP client.
    """
    bare = _strip_provider(model)
    match = _CLAUDE_ID.match(bare)
    if match is None:
        return RequestShape(model=bare, adaptive=False)
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    return RequestShape(model=bare, adaptive=major >= 5 or (major == 4 and minor >= 6))


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

    def fork(self) -> AnthropicClient:
        """Return a fresh client sharing the connection pool, fresh token state.

        The DI bean is a singleton, so its ``_tokens`` accumulator is
        process-global within the instance -- reusing it across concurrent
        queries would cross-contaminate per-query cost. A fork gives each
        query its own token tally while sharing the underlying
        ``httpx.Client`` (safe for concurrent use), so the existing
        connection pool is preserved.
        """
        return AnthropicClient(self._settings, http_client=self._http)

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
            **request_shape(model or self.root_model).body_for(max_tokens=max_tokens),
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
            **request_shape(model or self.sub_model).body_for(max_tokens=max_tokens),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = self._system(system)
        return self._text(body)
