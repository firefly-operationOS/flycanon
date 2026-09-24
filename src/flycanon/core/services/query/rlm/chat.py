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

"""The provider seam of the RLM answer engine.

The engine used to be one class against one URL. This module is the join:
:func:`build_rlm_client` reads ``FLYCANON_RLM_ROOT_MODEL`` /
``FLYCANON_RLM_SUB_MODEL``, resolves the ``<provider>:<model>`` prefix and
returns the client that speaks that provider, behind the
:class:`RlmChatClient` protocol the CodeAct loop and the answer service are
written against.

Why a seam and not a wider :class:`AnthropicClient`
--------------------------------------------------
Three shapes were on the table.

1. **Teach ``AnthropicClient`` a second URL.** Rejected: the retry loop, the
   auth header, the request body, the response parsing and the usage block
   all differ between the Anthropic Messages API and Azure OpenAI Chat
   Completions. One class holding two of each is a class whose every method
   starts with ``if``.
2. **Reuse ``fireflyframework_agentic.models.factory.ModelFactory``.**
   Rejected, on two measurements rather than taste. It returns a
   ``pydantic_ai.models.Model``, whose call surface is an agent run, not the
   raw tool-use turn the CodeAct loop drives -- the loop appends provider
   content blocks to its own transcript, answers each ``tool_use`` with a
   ``tool_result``, and re-reads the same transcript on the forced-final
   turn; and it is asynchronous, while this engine is deliberately
   synchronous because :class:`~flycanon.core.services.query.rlm_answer_service.RLMAnswerService`
   runs whole sessions in ``asyncio.to_thread``. On top of that, the factory
   does not exist in the ``fireflyframework-agentic`` version this project
   pins (26.6.14 ships no ``models`` package at all; ``models/factory.py``
   arrives in 26.06.15), so "reuse it" is also "bump the framework pin".
   The factory IS the right tool for the RAG answer path, which is a
   pydantic-ai agent already -- see
   :func:`flycanon.core.agents.builder.build_agent`.
3. **A seam: one protocol, one client per provider, one factory.** Chosen.
   Anthropic keeps its file untouched in behaviour, Azure gets a file whose
   whole job is the Chat Completions wire, and the thing that decides
   between them is fifteen lines that a reader can hold in their head.

The translation direction is deliberate
---------------------------------------
:class:`~flycanon.core.services.query.rlm.session.RLMSession` speaks the
Anthropic content-block vocabulary: an assistant turn is a list of ``text``
and ``tool_use`` blocks, a tool answer is a ``tool_result`` block. That
vocabulary is the engine's internal protocol, so
:class:`~flycanon.core.services.query.rlm.azure_client.AzureOpenAIChatClient`
translates Chat Completions into it and back. The alternative -- rewriting
the session against a third, neutral vocabulary -- would have touched the
one part of the engine that is load-bearing and well tested, to no end.

An unknown prefix is refused, loudly
------------------------------------
Until 26.8.0 ``AnthropicClient`` dropped ANY ``provider:`` prefix and POSTed
the bare id to ``api.anthropic.com``, so ``azure:gpt-5.4`` did not fail --
it asked Anthropic for a model Anthropic never had, retried six times and
surfaced a 404 blaming the wrong vendor. 26.8.0 refused every non-Anthropic
prefix at boot. This release serves ``azure:`` for real and keeps the
refusal for everything else: :func:`parse_model_ref` names the prefix it was
given and lists the ones that work.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)

#: The provider prefixes the RLM engine can route. ``azure-openai`` is an
#: accepted spelling of ``azure`` because
#: :func:`flycanon.core.services.embeddings.embedding_service._build_embedder`
#: accepts both, and one identifier grammar across the two paths is worth
#: more than a shorter table.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic", "azure", "azure-openai")

#: Canonical name per accepted spelling.
_CANONICAL: Mapping[str, str] = {
    "anthropic": "anthropic",
    "azure": "azure",
    "azure-openai": "azure",
}


class UnsupportedRlmProvider(ValueError):
    """An RLM model setting names a provider the engine cannot route."""


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A parsed ``<provider>:<model>`` RLM model setting.

    ``name`` is the bare id the provider is addressed with: an Anthropic
    model id, or -- on Azure -- the DEPLOYMENT name, which is what the Azure
    data plane puts in the URL and which need not resemble the model behind
    it.
    """

    provider: str
    name: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.provider}:{self.name}"


def parse_model_ref(model: str, *, setting: str) -> ModelRef:
    """Parse ``model``, or refuse the prefix by name.

    A bare id with no prefix is Anthropic: that is what the engine has always
    meant by ``claude-sonnet-4-6`` and the settings default carried the
    prefix explicitly long before it was enforced.

    Raises:
        UnsupportedRlmProvider: when the prefix is not in
            :data:`SUPPORTED_PROVIDERS`. The message names the setting, the
            prefix and every prefix that works -- the silent strip this
            replaces is the whole reason the seam exists.
    """
    provider, separator, bare = model.partition(":")
    if not separator:
        return ModelRef(provider="anthropic", name=model.strip())
    key = provider.strip().lower()
    canonical = _CANONICAL.get(key)
    if canonical is None:
        raise UnsupportedRlmProvider(
            f"{setting}={model!r} names provider {provider!r}, which the RLM answer engine "
            f"cannot route. Supported prefixes: {', '.join(SUPPORTED_PROVIDERS)}. "
            "Use anthropic:<model> for the Claude families or azure:<deployment> for an Azure "
            "OpenAI deployment (FLYCANON_AZURE_OPENAI_ENDPOINT + FLYCANON_AZURE_OPENAI_API_KEY, "
            "the same two settings the embedding path uses). Embeddings are configured "
            "separately through FLYCANON_EMBEDDING_MODEL and are unaffected by this setting."
        )
    if not bare.strip():
        raise UnsupportedRlmProvider(
            f"{setting}={model!r} has a {provider!r} prefix and no model after it. "
            "The general spelling is <provider>:<model>, and on Azure the second half is the "
            "DEPLOYMENT name, e.g. azure:my-gpt-5-deployment."
        )
    return ModelRef(provider=canonical, name=bare.strip())


def parse_price_table(raw: str, *, setting: str) -> dict[str, tuple[float, float]]:
    """Parse ``name=IN/OUT,name=IN/OUT`` USD-per-million-token prices.

    Azure prices are per deployment and per agreement, and a deployment name
    carries no model identity at all, so flycanon cannot ship a price table
    for them the way it ships one for Anthropic's public rates. An operator
    who wants cost on the Azure answer path states it here; one who does not
    still gets correct TOKEN accounting and a warning saying the cost column
    will read zero.

    Malformed entries are refused rather than skipped: a price table that
    quietly drops the row you mistyped is a bill that is quietly wrong.
    """
    table: dict[str, tuple[float, float]] = {}
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        name, separator, prices = entry.partition("=")
        input_price, price_separator, output_price = prices.partition("/")
        if not separator or not price_separator or not name.strip():
            raise ValueError(
                f"{setting}: {entry!r} is not ``<model>=<input>/<output>``. The prices are USD "
                "per million tokens, e.g. my-gpt-5-deployment=1.25/10.00."
            )
        try:
            parsed = (float(input_price.strip()), float(output_price.strip()))
        except ValueError as exc:
            raise ValueError(
                f"{setting}: {entry!r} does not carry two numbers. The prices are USD per "
                "million tokens, e.g. my-gpt-5-deployment=1.25/10.00."
            ) from exc
        if parsed[0] < 0 or parsed[1] < 0:
            raise ValueError(f"{setting}: {entry!r} carries a negative price.")
        table[name.strip()] = parsed
    return table


class TokenLedger:
    """Per-client token tallies and their cost, safe across the engine's threads.

    Shared by every provider client so ``token_totals()`` means the same
    thing whoever produced it: the orchestrator forks a client per query and
    :class:`~flycanon.core.services.query.rlm_answer_service.RLMAnswerService`
    merges the forks' totals into one cost event.

    A model with no row in ``prices`` contributes zero cost and is reported
    once through :meth:`unpriced`, because a cost of zero that nobody
    mentions is indistinguishable from a query that was free.
    """

    def __init__(self, prices: Mapping[str, tuple[float, float]]) -> None:
        self._prices = dict(prices)
        self._tokens: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()
        self._unpriced: set[str] = set()

    def record(self, model: str, *, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            bucket = self._tokens.setdefault(model, {"input": 0, "output": 0})
            bucket["input"] += int(input_tokens or 0)
            bucket["output"] += int(output_tokens or 0)
            if model not in self._prices:
                self._unpriced.add(model)

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._unpriced.clear()

    def unpriced(self) -> list[str]:
        """Models that were billed at zero because no price is configured."""
        with self._lock:
            return sorted(self._unpriced)

    def totals(self) -> dict:
        with self._lock:
            snapshot = {model: dict(tally) for model, tally in self._tokens.items()}
        total_in = sum(tally["input"] for tally in snapshot.values())
        total_out = sum(tally["output"] for tally in snapshot.values())
        cost = sum(
            tally["input"] / 1e6 * self._prices.get(model, (0.0, 0.0))[0]
            + tally["output"] / 1e6 * self._prices.get(model, (0.0, 0.0))[1]
            for model, tally in snapshot.items()
        )
        return {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "estimated_cost_usd": round(cost, 4),
            "by_model": snapshot,
        }


@runtime_checkable
class RlmChatClient(Protocol):
    """What the CodeAct loop and the answer service need from a provider.

    Written down because it is now implemented twice. Every method is
    synchronous on purpose: the engine is driven from ``asyncio.to_thread``,
    so blocking I/O here is the design and not an oversight.
    """

    #: Bare provider-side id of the orchestrator model (an Anthropic model id,
    #: or an Azure deployment name). Read for logging and cost attribution.
    root_model: str
    #: Bare provider-side id of the sub-call model.
    sub_model: str

    def fork(self) -> RlmChatClient:
        """A sibling client with a fresh token tally and the same connection pool."""
        ...

    def chat_raw(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        model: str | None = None,
        max_tokens: int = 1500,
    ) -> dict:
        """One tool-enabled turn, in the Anthropic content-block vocabulary.

        Returns a mapping with ``content`` (a list of ``text`` / ``tool_use``
        blocks) and ``stop_reason``, whatever provider produced it.
        """
        ...

    def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """One single-shot completion; returns the concatenated text."""
        ...

    def reset_tokens(self) -> None: ...

    def token_totals(self) -> dict:
        """``input_tokens`` / ``output_tokens`` / ``estimated_cost_usd`` / ``by_model``."""
        ...


def build_rlm_client(settings: CanonSettings, http_client: httpx.Client | None = None) -> RlmChatClient:
    """The client the configured RLM models need, or a refusal naming the setting.

    Both models must sit on one provider. A client carries one base URL and
    one credential, so a root on Anthropic with a sub on Azure would need a
    dispatching client that owns two of each and merges their ledgers -- a
    feature nobody has asked for, and one that would be wrong to fake with a
    half-implementation. The mix is refused here, in one sentence, instead.
    """
    root = parse_model_ref(settings.rlm_root_model, setting="FLYCANON_RLM_ROOT_MODEL")
    sub = parse_model_ref(settings.rlm_sub_model, setting="FLYCANON_RLM_SUB_MODEL")
    if root.provider != sub.provider:
        raise UnsupportedRlmProvider(
            f"FLYCANON_RLM_ROOT_MODEL={settings.rlm_root_model!r} is on {root.provider!r} and "
            f"FLYCANON_RLM_SUB_MODEL={settings.rlm_sub_model!r} is on {sub.provider!r}. "
            "The RLM engine holds one provider client -- one endpoint, one credential -- so both "
            "models must name the same provider. Put the orchestrator and the sub-calls on the "
            "same one."
        )
    if root.provider == "azure":
        from flycanon.core.services.query.rlm.azure_client import AzureOpenAIChatClient

        logger.info(
            "rlm answer engine on Azure OpenAI: root deployment=%s sub deployment=%s",
            root.name,
            sub.name,
        )
        return AzureOpenAIChatClient(settings, http_client=http_client)

    from flycanon.core.services.query.rlm.client import AnthropicClient

    return AnthropicClient(settings, http_client=http_client)


__all__ = [
    "SUPPORTED_PROVIDERS",
    "ModelRef",
    "RlmChatClient",
    "TokenLedger",
    "UnsupportedRlmProvider",
    "build_rlm_client",
    "parse_model_ref",
    "parse_price_table",
]
