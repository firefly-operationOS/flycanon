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

"""Azure OpenAI Chat Completions, behind the RLM engine's chat-client seam.

The counterpart of :mod:`flycanon.core.services.query.rlm.client`: same
public surface (:class:`~flycanon.core.services.query.rlm.chat.RlmChatClient`),
same synchronous ``httpx`` posture, a different wire. It exists so
``FLYCANON_RLM_ROOT_MODEL=azure:<deployment>`` reaches Azure instead of
being a configuration that silently meant Anthropic.

It reads exactly the settings the Azure EMBEDDING path reads --
``FLYCANON_AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_ENDPOINT``,
``FLYCANON_AZURE_OPENAI_API_KEY`` / ``AZURE_OPENAI_API_KEY``,
``FLYCANON_AZURE_OPENAI_API_VERSION`` and ``FLYCANON_AZURE_AUTH`` -- and
validates them through the same
:func:`~flycanon.core.services.embeddings.azure.require_azure_configuration`.
A second spelling for one Azure account is a second thing to get wrong, and
a deployment that already embeds against this resource should not have to
say where it lives twice.

The translation, and what it costs
==================================
:class:`~flycanon.core.services.query.rlm.session.RLMSession` speaks
Anthropic content blocks (see :mod:`flycanon.core.services.query.rlm.chat`
for why that is the engine's internal vocabulary), so this client converts
in both directions:

============================================  ============================================
RLM / Anthropic                               Azure OpenAI Chat Completions
============================================  ============================================
``system`` field                              a leading ``{"role": "system"}`` message
assistant ``tool_use`` block                  ``tool_calls[].function`` (arguments = JSON text)
user ``tool_result`` block                    its own ``{"role": "tool", "tool_call_id": ...}`` message
tool ``input_schema``                         ``tools[].function.parameters``
``stop_reason``                               ``finish_reason``, renamed
``usage.input_tokens`` / ``output_tokens``    ``usage.prompt_tokens`` / ``completion_tokens``
============================================  ============================================

Three deliberate differences from the Anthropic client:

* **No sampling knobs, ever.** The Anthropic client sends
  ``temperature: 0.0`` to pre-4.6 Claude models because a deterministic
  sampler makes a CodeAct loop reproducible. Here it cannot: a DEPLOYMENT
  NAME SAYS NOTHING ABOUT THE MODEL BEHIND IT, and the GPT-5 / o-series
  reasoning models answer ``400 Unsupported parameter: 'temperature'``. A
  guess that is wrong fails every single turn, so the parameter is omitted
  and the deployment's own default stands.
* **``max_completion_tokens``, not ``max_tokens``.** The reasoning models
  refuse ``max_tokens`` outright; ``max_completion_tokens`` is accepted by
  every chat model on the data-plane versions this project defaults to.
* **No ``cache_control`` breakpoint.** ``FLYCANON_RLM_PROMPT_CACHE`` marks
  the system prompt for Anthropic's explicit cache. Azure OpenAI caches
  long prompt prefixes automatically and has no such field, so the setting
  simply does not apply on this path -- it is not read here, rather than
  read and dropped.

Cost
====
Azure prices are per deployment and per agreement, and a deployment name
carries no model identity, so there is no table to ship. Token accounting is
exact; the cost column reads zero until an operator states the rates in
``FLYCANON_AZURE_MODEL_PRICES``, and until they do, this client says so once
per deployment at WARNING rather than letting a free-looking bill go by.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.azure import (
    entra_token_provider,
    require_azure_configuration,
)
from flycanon.core.services.query.rlm.chat import (
    TokenLedger,
    parse_model_ref,
    parse_price_table,
)

logger = logging.getLogger(__name__)

#: ``finish_reason`` -> the ``stop_reason`` the session's callers read. Only
#: ``tool_calls`` is load-bearing (the loop branches on the presence of
#: ``tool_use`` blocks, not on this), but a transcript that reads like the
#: Anthropic one is a transcript a reader can debug with one set of habits.
_STOP_REASON: dict[str, str] = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filter",
    "function_call": "tool_use",
}


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    """Anthropic tool definitions as OpenAI function definitions."""
    converted: list[dict] = []
    for tool in tools:
        function: dict[str, Any] = {"name": tool.get("name", "")}
        if tool.get("description"):
            function["description"] = tool["description"]
        # ``input_schema`` and ``parameters`` are the same JSON Schema under
        # two names. A tool with neither is sent with an empty object schema,
        # which is what both APIs mean by "this tool takes no arguments".
        function["parameters"] = tool.get("input_schema") or {"type": "object", "properties": {}}
        converted.append({"type": "function", "function": function})
    return converted


def _messages_to_openai(messages: list[dict], system: str) -> list[dict]:
    """The RLM transcript as an OpenAI ``messages`` array.

    The shapes that actually occur in
    :meth:`~flycanon.core.services.query.rlm.session.RLMSession._run_loop`
    are: a user turn carrying a string, an assistant turn carrying the
    provider's content blocks, and a user turn carrying a list of
    ``tool_result`` blocks. Each ``tool_result`` becomes its own ``tool``
    message, because that is how Chat Completions answers a tool call --
    one message per ``tool_call_id``, and a missing one is a 400 on the next
    turn exactly as a missing ``tool_result`` is at Anthropic.
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = content or []
        if role == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            tool_calls = [
                {
                    "id": b.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": b.get("name", ""),
                        "arguments": json.dumps(b.get("input") or {}),
                    },
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            # ``content: null`` is how Chat Completions spells "this turn is
            # only tool calls", and it is a 400 on a turn that has none --
            # so a text-less, call-less assistant turn is sent as "" rather
            # than as a message the API will refuse.
            turn: dict[str, Any] = {"role": "assistant", "content": text or (None if tool_calls else "")}
            if tool_calls:
                turn["tool_calls"] = tool_calls
            out.append(turn)
            continue
        # A user turn is either plain text blocks or the tool answers.
        text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        for block in blocks:
            if block.get("type") != "tool_result":
                continue
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    # ``is_error`` has no field of its own here; the error text
                    # is the content, which is what the model reads either way.
                    "content": str(block.get("content", "")),
                }
            )
        if text_parts:
            out.append({"role": role, "content": "".join(text_parts)})
    return out


def _response_to_anthropic(payload: dict, *, deployment: str) -> dict:
    """One Chat Completions choice as an Anthropic-shaped response.

    A 200 with NO choices is a real Azure answer -- it is what a content
    filter on the PROMPT returns, with the reason in
    ``prompt_filter_results``. Translating it to an empty block list would
    hand the CodeAct loop a turn in which the model said nothing, which the
    loop reads as "answered in plain text" and turns into an empty answer
    with no error anywhere. So it is raised instead, with the deployment and
    the filter verdict in the message.
    """
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(
            f"azure openai returned no choices for deployment {deployment!r}; this is normally a "
            f"prompt content filter. prompt_filter_results={payload.get('prompt_filter_results')!r}"
        )
    message = (choices[0] or {}).get("message", {})
    blocks: list[dict] = []
    text = message.get("content")
    if text:
        blocks.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError):
            # A model that emits unparseable arguments has produced a turn the
            # REPL cannot run. Surfaced at WARNING with the deployment named,
            # and passed on as an empty call: the loop answers it with an empty
            # stdout and gets another turn, which is a far better outcome than
            # failing the whole query on one malformed tool call.
            logger.warning(
                "azure deployment %s returned tool arguments that are not JSON (%d chars); "
                "the call is forwarded with no arguments",
                deployment,
                len(str(raw_arguments)),
            )
            arguments = {}
        if not isinstance(arguments, dict):
            logger.warning(
                "azure deployment %s returned tool arguments of type %s, not an object; "
                "the call is forwarded with no arguments",
                deployment,
                type(arguments).__name__,
            )
            arguments = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": function.get("name", ""),
                "input": arguments,
            }
        )
    finish = (choices[0] or {}).get("finish_reason")
    return {"content": blocks, "stop_reason": _STOP_REASON.get(finish or "", finish or "end_turn")}


class AzureOpenAIChatClient:
    """Synchronous Azure OpenAI Chat Completions client with token accounting.

    Implements :class:`~flycanon.core.services.query.rlm.chat.RlmChatClient`.
    One instance owns its ``httpx.Client`` and its ledger; :meth:`fork`
    hands out siblings that share the pool and start a fresh tally, which is
    what keeps per-query cost from cross-contaminating on the
    self-consistency path.
    """

    def __init__(
        self,
        settings: CanonSettings,
        http_client: httpx.Client | None = None,
        *,
        token_provider: Callable[[], str] | None = None,
    ):
        self._settings = settings
        root = parse_model_ref(settings.rlm_root_model, setting="FLYCANON_RLM_ROOT_MODEL")
        sub = parse_model_ref(settings.rlm_sub_model, setting="FLYCANON_RLM_SUB_MODEL")
        # Both are Azure here -- build_rlm_client refuses a mixed pair before
        # it picks this class -- so the bare halves are deployment names.
        self.root_model = root.name
        self.sub_model = sub.name
        require_azure_configuration(
            endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            api_key=settings.azure_openai_api_key,
            auth=settings.azure_auth,
        )
        self._endpoint = settings.azure_openai_endpoint.strip().rstrip("/")
        self._api_version = settings.azure_openai_api_version.strip()
        self._managed_identity = settings.azure_auth == "managed_identity"
        self._api_key = "" if self._managed_identity else settings.azure_openai_api_key
        # ``token_provider`` is passed by :meth:`fork` so the credential --
        # and the token cache inside ``DefaultAzureCredential`` -- is built
        # once per process rather than once per forked client. The
        # self-consistency path forks N clients per query, and N new
        # credentials per query would mean N cold token acquisitions.
        self._token_provider: Callable[[], str] | None = token_provider
        if self._managed_identity and self._token_provider is None:
            self._token_provider = entra_token_provider()
        self._http = http_client or httpx.Client(timeout=180.0)
        self._prices = parse_price_table(settings.azure_model_prices, setting="FLYCANON_AZURE_MODEL_PRICES")
        self._ledger = TokenLedger(self._prices)
        self._warned_unpriced: set[str] = set()

    def fork(self) -> AzureOpenAIChatClient:
        """A sibling with a fresh tally over the same connection pool and credential."""
        return AzureOpenAIChatClient(
            self._settings, http_client=self._http, token_provider=self._token_provider
        )

    # -- token accounting ----------------------------------------------
    def _record_usage(self, model: str, usage: dict) -> None:
        """Record one turn's usage, translating Azure's spelling of it.

        Chat Completions reports ``prompt_tokens`` / ``completion_tokens``
        where the Messages API reports ``input_tokens`` / ``output_tokens``.
        Reading only the Anthropic names here would have produced a ledger of
        zeroes that looked exactly like a client nobody called.
        """
        self._ledger.record(
            model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
        if model not in self._prices and model not in self._warned_unpriced:
            self._warned_unpriced.add(model)
            logger.warning(
                "azure deployment %s has no configured price, so its cost is recorded as 0.00 "
                "while its tokens are counted exactly. Azure rates are per deployment and per "
                "agreement, so flycanon cannot infer them: set FLYCANON_AZURE_MODEL_PRICES="
                "%s=<usd-per-million-input>/<usd-per-million-output> to bill this path.",
                model,
                model,
            )

    def reset_tokens(self) -> None:
        self._ledger.reset()

    def token_totals(self) -> dict:
        return self._ledger.totals()

    # -- HTTP ----------------------------------------------------------
    def _url(self, deployment: str) -> str:
        return (
            f"{self._endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self._api_version}"
        )

    def _headers(self) -> dict[str, str]:
        """The auth header for the configured mode.

        The bearer token is fetched per request because
        ``DefaultAzureCredential`` is what owns caching and refresh -- the
        same contract the Azure embedder honours.
        """
        headers = {"content-type": "application/json"}
        if self._token_provider is not None:
            headers["Authorization"] = f"Bearer {self._token_provider()}"
        else:
            headers["api-key"] = self._api_key
        return headers

    def _request(self, deployment: str, body: dict) -> dict:
        """POST with retry/backoff on 429/5xx; return the parsed response JSON.

        The backoff schedule is the Anthropic client's, so an operator moving
        between providers does not also move to a different tail latency
        under throttling. 408 joins the retriable set because Azure answers a
        long generation on a busy deployment with a request timeout rather
        than a 5xx.
        """
        url = self._url(deployment)
        last = ""
        for attempt in range(6):
            try:
                response = self._http.post(url, json=body, headers=self._headers())
                if response.status_code == 200:
                    payload = response.json()
                    self._record_usage(deployment, payload.get("usage") or {})
                    return payload
                last = f"{response.status_code}: {response.text[:200]}"
                if response.status_code not in (408, 429, 500, 502, 503, 529):
                    break
            except httpx.HTTPError as exc:  # noqa: PERF203
                last = str(exc)
            time.sleep(min(2**attempt, 30))
        raise RuntimeError(f"azure openai call failed for deployment {deployment!r}: {last}")

    def _deployment(self, model: str | None, default: str) -> str:
        """Resolve a per-call model override to a deployment name.

        An override may carry a prefix; ``anthropic:`` on this client is a
        configuration error and is refused by name rather than quietly sent
        to Azure as a deployment called ``claude-sonnet-5``.
        """
        if model is None:
            return default
        ref = parse_model_ref(model, setting="model=")
        if ref.provider != "azure":
            raise ValueError(
                f"model={model!r} names provider {ref.provider!r}, but this RLM run is on Azure "
                "OpenAI. A per-call override must name an Azure deployment "
                "(azure:<deployment>), or omit the prefix."
            )
        return ref.name

    # -- public surface ------------------------------------------------
    def chat_raw(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str | None = None,
        max_tokens: int = 1500,
    ) -> dict:
        """Tool-enabled turn, answered in the Anthropic content-block shape."""
        deployment = self._deployment(model, self.root_model)
        body: dict[str, Any] = {
            "messages": _messages_to_openai(messages, system),
            "max_completion_tokens": max_tokens,
        }
        if tools:
            body["tools"] = _tools_to_openai(tools)
            body["tool_choice"] = "auto"
        return _response_to_anthropic(self._request(deployment, body), deployment=deployment)

    def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """Single-shot completion -- a recursive sub-call on a chunk of context."""
        deployment = self._deployment(model, self.sub_model)
        body: dict[str, Any] = {
            "messages": _messages_to_openai([{"role": "user", "content": prompt}], system),
            "max_completion_tokens": max_tokens,
        }
        payload = self._request(deployment, body)
        translated = _response_to_anthropic(payload, deployment=deployment)
        return "".join(
            block.get("text", "") for block in translated["content"] if block.get("type") == "text"
        )


__all__ = ["AzureOpenAIChatClient"]
