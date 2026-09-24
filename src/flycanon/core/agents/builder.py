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

"""Centralised :class:`FireflyAgent` constructor.

Every stage that talks to an LLM (consolidation, RAG answer) goes
through :func:`build_agent`. The helper folds the operator-tunable
output-token budget into ``model_settings`` and keeps the
construction recipe identical across stages so a tuning change
lands in one place.

Background -- why ``azure:`` is built here rather than inferred
==============================================================
pydantic-ai resolves a ``provider:model`` string itself, and its
``AzureProvider`` reads ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY``
and ``OPENAI_API_VERSION`` from the bare environment. flycanon configures the
same account as ``FLYCANON_AZURE_OPENAI_ENDPOINT`` /
``FLYCANON_AZURE_OPENAI_API_KEY`` / ``FLYCANON_AZURE_OPENAI_API_VERSION``
(with the bare ``AZURE_*`` names accepted as a fallback), and has no
``OPENAI_API_VERSION`` at all. A deployment that configured flycanon's way
therefore had working Azure EMBEDDINGS and an answer path that raised
``UserError: Must provide one of the api_version argument or the
OPENAI_API_VERSION environment variable`` on the first query.

So an ``azure:`` id is built explicitly from :class:`CanonSettings` here --
one Azure account, one place it is described, the same two credentials the
embedding path uses. Every other provider id is still handed to pydantic-ai
as a string, because for those the SDK's own environment contract is the
right one and duplicating it would be the bug this fixes, inverted.

Background -- why ``max_tokens`` matters
========================================
Anthropic's API defaults to ``max_tokens=4096`` and OpenAI clamps
similarly. For structured outputs (pydantic-ai's ``output_type=...``
contract -- a JSON envelope the model must produce in one shot) the
4096 ceiling truncates the response mid-array on dense business
documents. pydantic-ai then retries; when retries also overflow,
the parsed output ends up empty (``candidates=[]``) and the user
sees a silent zero-result. Bumping to ``max_tokens=8192`` (Sonnet
4.6's public ceiling) stops the silent-truncation tail; operators
on models with a higher ceiling can raise the env var further.
"""

from __future__ import annotations

from typing import Any

from flycanon.config import CanonSettings


def build_agent(
    *,
    name: str,
    model: str,
    output_type: type,
    instructions: str,
    settings: CanonSettings,
    max_output_tokens: int | None = None,
    extra_settings: dict[str, Any] | None = None,
) -> Any:
    """Construct a :class:`FireflyAgent` with the standard knobs.

    Args:
        name: Identifier passed to ``FireflyAgent`` -- shows up in
            tracing + metrics. Use a stable kebab/snake-cased value
            (``flycanon-consolidator``, ``flycanon-answerer``).
        model: Provider:model id (e.g. ``anthropic:claude-sonnet-4-6``).
        output_type: pydantic model the agent's structured output is
            validated against. The agent's ``run()`` returns an
            instance of this type.
        instructions: Rendered system prompt.
        settings: :class:`CanonSettings` instance the agent reads
            cross-cutting knobs from. Required so the helper has
            access to the configured output-token budget without
            requiring callers to plumb the env var themselves.
        max_output_tokens: Optional override for this specific call.
            ``None`` falls back to ``settings.agent_max_output_tokens``.
        extra_settings: Optional extra ``model_settings`` entries.
            Caller-provided keys WIN on conflict so a stage can cap
            below the global budget (e.g. a 1-token classifier).

    The agent is constructed with ``auto_register=False`` because
    each stage builds a fresh agent per call. Auto-registering would
    raise duplicate-name errors when the same stage is exercised by
    sync + async paths in the same process.
    """
    try:
        from fireflyframework_agentic.agents import FireflyAgent
    except ImportError as exc:  # pragma: no cover -- runtime dep guard
        raise RuntimeError("fireflyframework_agentic is required to build FireflyAgent instances") from exc

    resolved_max = resolve_max_output_tokens(settings, override=max_output_tokens)
    resolved_model: Any = _resolve_model(model, settings)
    model_settings: dict[str, Any] = {"max_tokens": resolved_max}
    if extra_settings:
        # Caller-provided settings win on conflict -- a stage can cap
        # itself below the default by passing ``max_tokens=128`` in
        # ``extra_settings``.
        model_settings.update(extra_settings)

    return FireflyAgent(
        name,
        model=resolved_model,
        instructions=instructions,
        output_type=output_type,
        model_settings=model_settings,
        auto_register=False,
    )


def _resolve_model(model: str, settings: CanonSettings) -> Any:
    """``azure:<deployment>`` as a configured model object; anything else unchanged.

    Returns the id untouched for every other provider so pydantic-ai keeps
    doing its own inference -- this function exists only to close the gap
    described in the module docstring, and widening it would mean flycanon
    re-implementing provider resolution it does not own.
    """
    provider, separator, deployment = model.partition(":")
    if not separator or provider.strip().lower() not in ("azure", "azure-openai"):
        return model
    if not deployment.strip():
        raise ValueError(
            f"model={model!r} has an azure: prefix and no deployment after it. On Azure the "
            "second half is the DEPLOYMENT name, e.g. azure:my-gpt-5-deployment."
        )

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.azure import AzureProvider

    from flycanon.core.services.embeddings.azure import (
        entra_token_provider,
        require_azure_configuration,
    )

    require_azure_configuration(
        endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key,
        auth=settings.azure_auth,
    )
    if settings.azure_auth == "managed_identity":
        # AzureProvider only takes a key, so the identity path builds the SDK
        # client itself -- the same move, and the same reason, as
        # :class:`~flycanon.core.services.embeddings.azure.FlycanonAzureEmbedder`.
        from openai import AsyncAzureOpenAI

        provider_obj = AzureProvider(
            openai_client=AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                azure_ad_token_provider=entra_token_provider(),
            )
        )
    else:
        provider_obj = AzureProvider(
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            api_key=settings.azure_openai_api_key,
        )
    return OpenAIChatModel(deployment.strip(), provider=provider_obj)


def resolve_max_output_tokens(
    settings: CanonSettings,
    *,
    override: int | None = None,
) -> int:
    """Return the effective ``max_tokens`` value for an agent call.

    Resolution order (first non-None wins):

    1. ``override`` -- the caller's explicit per-call ceiling.
    2. ``settings.agent_max_output_tokens`` -- the global default.

    The per-stage env-var overrides (``consolidator_max_output_tokens``,
    ``answer_max_output_tokens``) are NOT consulted here -- callers
    pass them explicitly via ``override`` so the resolution stays
    one-way and predictable. This avoids the resolver guessing which
    stage is asking.
    """
    if override is not None:
        return override
    return settings.agent_max_output_tokens
