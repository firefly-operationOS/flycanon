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

"""Azure OpenAI embeddings, configured rather than guessed.

The framework's :class:`AzureEmbedder` takes an endpoint, an api-version
defaulting to ``2024-02-01`` and an api-key, and forwards ``dimensions``
whenever it is set. flycanon used to construct it from a bare
``os.environ.get("AZURE_OPENAI_ENDPOINT", "")`` -- an unset endpoint became
the empty string and failed inside the SDK, the api-version was unreachable
from configuration, and ``dimensions`` was sent to models that answer ``400``
to it.

This subclass closes those three, and adds the one Azure feature a security
review asks for:

* **Managed identity.** ``FLYCANON_AZURE_AUTH=managed_identity`` acquires a
  bearer token through ``DefaultAzureCredential`` for the
  ``https://cognitiveservices.azure.com/.default`` scope, so no key exists in
  the environment to leak. The token provider is called per request by the
  OpenAI SDK and ``DefaultAzureCredential`` caches and refreshes underneath.
  Needs ``azure-identity`` (``uv sync --extra azure``); its absence is an
  ImportError naming the extra, at construction, not at the first call.
* **``dimensions`` is omitted for models that reject it** (see
  :mod:`flycanon.core.services.embeddings.model_capabilities`).

**The grammar nothing used to document:** on Azure the ``model=`` argument is
the DEPLOYMENT name. ``azure:text-embedding-3-large`` works only when the
deployment is named after the model; ``azure:my-emb-3-large-deploy`` is the
general spelling.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fireflyframework_agentic.embeddings.providers.azure import AzureEmbedder
from fireflyframework_agentic.exceptions import EmbeddingProviderError

from flycanon.core.services.embeddings.model_capabilities import capabilities_for

logger = logging.getLogger(__name__)

#: The resource scope an Entra token must carry to call Azure OpenAI.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureConfigurationError(ValueError):
    """Raised when an ``azure:`` model is configured without what it needs."""


def require_azure_configuration(*, endpoint: str, api_version: str, api_key: str, auth: str) -> None:
    """Refuse an Azure embedder that cannot possibly work, at config time.

    Every message names the setting, because the failure this replaces was an
    ``openai.APIConnectionError`` against the empty string, raised on the
    first ingest of a deployment that had been green for hours.
    """
    if not endpoint.strip():
        raise AzureConfigurationError(
            "an azure: model is configured but FLYCANON_AZURE_OPENAI_ENDPOINT is empty. "
            "Set it to the resource endpoint, e.g. https://my-resource.openai.azure.com "
            "(AZURE_OPENAI_ENDPOINT is accepted as a fallback)."
        )
    if not endpoint.strip().lower().startswith("https://"):
        raise AzureConfigurationError(
            f"FLYCANON_AZURE_OPENAI_ENDPOINT={endpoint!r} is not an https:// URL. "
            "Azure OpenAI is https-only and a plaintext endpoint would send the key in the clear."
        )
    if not api_version.strip():
        raise AzureConfigurationError(
            "FLYCANON_AZURE_OPENAI_API_VERSION is empty; set the data-plane api-version, e.g. 2026-05-01."
        )
    if auth == "api_key" and not api_key.strip():
        raise AzureConfigurationError(
            "FLYCANON_AZURE_AUTH=api_key but no key is configured. Set "
            "FLYCANON_AZURE_OPENAI_API_KEY (AZURE_OPENAI_API_KEY is accepted as a fallback), "
            "or switch to FLYCANON_AZURE_AUTH=managed_identity."
        )


def entra_token_provider() -> Callable[[], str]:
    """A bearer-token callable for the Azure OpenAI data plane.

    Returned rather than a token, because the SDK calls it per request and
    ``DefaultAzureCredential`` is what owns caching and refresh.
    """
    try:
        from azure.identity import (  # type: ignore[import]  # optional 'azure' extra
            DefaultAzureCredential,
            get_bearer_token_provider,
        )
    except ImportError as exc:  # pragma: no cover - exercised by the extra being absent
        raise ImportError(
            "FLYCANON_AZURE_AUTH=managed_identity needs the azure-identity package. "
            "Install it with `uv sync --extra azure`."
        ) from exc
    return get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE)


class FlycanonAzureEmbedder(AzureEmbedder):
    """:class:`AzureEmbedder` with managed identity and capability-aware requests."""

    def __init__(
        self,
        model: str,
        dimensions: int | None = None,
        *,
        azure_endpoint: str,
        api_version: str,
        api_key: str | None = None,
        token_provider: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model,
            dimensions,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            api_key=api_key or ("placeholder" if token_provider is not None else None),
            **kwargs,
        )
        if token_provider is not None:
            # Rebuild the client on the identity path. The base class only
            # knows how to construct a keyed client, and AsyncAzureOpenAI
            # refuses to be handed both an api_key and a token provider.
            from openai import AsyncAzureOpenAI

            self._client = AsyncAzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_version=api_version,
                azure_ad_token_provider=token_provider,
            )
            logger.info(
                "azure embedder %s authenticates with a managed identity (scope %s)",
                model,
                COGNITIVE_SERVICES_SCOPE,
            )
        self._send_dimensions = capabilities_for(model).supports_dimensions_param
        if dimensions is not None and not self._send_dimensions:
            logger.info(
                "azure deployment %s does not accept a `dimensions` parameter; it is omitted and "
                "the model's native width is used",
                model,
            )

    async def _embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """One batch, with ``dimensions`` sent only to models that take it.

        Restated rather than delegated because the base method's one
        difference from this one is the thing that 400s on ``ada-002``.
        """
        try:
            params: dict[str, Any] = {"input": texts, "model": self._model}
            if self._dimensions is not None and self._send_dimensions:
                params["dimensions"] = self._dimensions
            response = await self._client.embeddings.create(**params)
            ordered = sorted(response.data, key=lambda item: item.index)
            return [item.embedding for item in ordered]
        except Exception as exc:
            raise EmbeddingProviderError(f"Azure embedding failed: {exc}") from exc
