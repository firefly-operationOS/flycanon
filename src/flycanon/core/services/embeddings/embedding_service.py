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

"""Embedding orchestration.

Resolves a ``<provider>:<model>`` identifier to the concrete embedder
implementation shipped by ``fireflyframework_agentic.embeddings`` and
exposes a single :meth:`embed` method the ingestion / query stages
call.

Provider selection is deliberately narrow at this stage -- the embedder
adapters exposed by ``fireflyframework_agentic.embeddings`` (OpenAI,
Cohere, Google, Mistral, Voyage, Azure OpenAI, Bedrock, Ollama) are
wired. Adding a new provider is a two-line change: import the class, add
a branch in :func:`build_embedding_service`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Wraps every embedder failure so callers don't depend on the agentic
    framework's exception tree."""


class EmbeddingService:
    """Bounded surface over ``fireflyframework_agentic`` embedders."""

    def __init__(self, *, embedder: object, model: str, dimensions: int) -> None:
        self._embedder = embedder
        self._model = model
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    # Most embedders have a hard token / character limit per input
    # (nomic-embed-text: ~8 192 tokens, OpenAI text-embedding-3-*:
    # 8 191 tokens, Voyage / Cohere: 8 000 tokens). 8 000 characters
    # is a conservative cap that keeps every supported provider happy
    # without truncating typical chunked content.
    _MAX_INPUT_CHARS = 8000

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` in one call. Returns one vector per input.

        The underlying embedder handles batching; we still split
        absurdly large inputs into 64-element windows so memory use
        stays bounded. Each input is also truncated to
        :attr:`_MAX_INPUT_CHARS` so a single oversized chunk doesn't
        cause the whole batch to fail with a 400 from the provider.
        On batch failure we retry one input at a time and emit a
        zero-vector for the offending entries (with a warning) so a
        single bad chunk never blocks an ingest.
        """
        if not texts:
            return []
        items = [self._truncate(t) for t in texts]
        vectors: list[list[float]] = []
        window = 64
        for start in range(0, len(items), window):
            batch = items[start : start + window]
            try:
                batch_vectors = await self._embed_batch(batch)
            except Exception as exc:
                logger.warning(
                    "embedding batch failed (%s); falling back to one-by-one",
                    exc,
                )
                batch_vectors = await self._embed_one_by_one(batch)
            vectors.extend(batch_vectors)
        if len(vectors) != len(items):
            raise EmbeddingError(f"embedder returned {len(vectors)} vectors for {len(items)} inputs")
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        result = await self._embedder.embed(batch)  # type: ignore[attr-defined]
        chunk_vectors = getattr(result, "embeddings", None)
        if chunk_vectors is None:
            raise EmbeddingError("embedder result missing ``embeddings`` attribute")
        return [list(v) for v in chunk_vectors]

    async def _embed_one_by_one(self, batch: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        zero = [0.0] * self._dimensions
        for idx, text in enumerate(batch):
            try:
                result = await self._embedder.embed([text])  # type: ignore[attr-defined]
                vec = getattr(result, "embeddings", None)
                if not vec:
                    raise EmbeddingError("empty embedding response")
                out.append(list(vec[0]))
            except Exception as exc:
                logger.warning(
                    "embedding failed for item %d (%d chars): %s; using zero vector",
                    idx,
                    len(text),
                    exc,
                )
                out.append(list(zero))
        return out

    def _truncate(self, text: str) -> str:
        s = text or ""
        if len(s) <= self._MAX_INPUT_CHARS:
            return s or " "  # provider rejects empty string
        return s[: self._MAX_INPUT_CHARS]

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


def _build_embedder(*, provider: str, model: str, dimensions: int, batch_size: int) -> object:
    """Pick the concrete embedder for ``provider``.

    Heavy imports are deferred so a configuration that never touches a
    given provider doesn't pull its SDK off the wheel. Module names
    follow ``fireflyframework_agentic.embeddings.providers.<provider>``
    (one module per provider, no ``_embedder`` suffix).
    """
    import os

    p = provider.strip().lower()
    if p == "openai":
        from fireflyframework_agentic.embeddings.providers.openai import OpenAIEmbedder

        return OpenAIEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "azure" or p == "azure-openai":
        from fireflyframework_agentic.embeddings.providers.azure import AzureEmbedder

        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        return AzureEmbedder(
            model=model,
            dimensions=dimensions,
            batch_size=batch_size,
            azure_endpoint=azure_endpoint,
        )
    if p == "cohere":
        from fireflyframework_agentic.embeddings.providers.cohere import CohereEmbedder

        return CohereEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "google" or p == "gemini":
        from fireflyframework_agentic.embeddings.providers.google import GoogleEmbedder

        return GoogleEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "mistral":
        from fireflyframework_agentic.embeddings.providers.mistral import MistralEmbedder

        return MistralEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "voyage":
        from fireflyframework_agentic.embeddings.providers.voyage import VoyageEmbedder

        return VoyageEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "bedrock":
        from fireflyframework_agentic.embeddings.providers.bedrock import BedrockEmbedder

        return BedrockEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "ollama":
        from fireflyframework_agentic.embeddings.providers.ollama import OllamaEmbedder

        # Ollama runs as a sidecar; honour FLYCANON_OLLAMA_BASE_URL.
        base_url = os.environ.get("FLYCANON_OLLAMA_BASE_URL", "http://localhost:11434")
        return OllamaEmbedder(
            model=model,
            dimensions=dimensions,
            base_url=base_url,
            batch_size=batch_size,
        )
    raise EmbeddingError(
        f"unknown embedding provider {provider!r}; "
        "supported: openai, azure, cohere, google, mistral, voyage, bedrock, ollama"
    )


def build_embedding_service(
    *,
    embedding_model: str,
    dimensions: int,
    batch_size: int,
) -> EmbeddingService:
    """Parse the ``<provider>:<model>`` identifier and instantiate the service."""
    if ":" not in embedding_model:
        raise EmbeddingError(
            f"FLYCANON_EMBEDDING_MODEL must be ``<provider>:<model>`` (got {embedding_model!r})"
        )
    provider, _, model = embedding_model.partition(":")
    embedder = _build_embedder(
        provider=provider,
        model=model,
        dimensions=dimensions,
        batch_size=batch_size,
    )
    logger.info(
        "embedding service ready provider=%s model=%s dimensions=%d batch_size=%d",
        provider,
        model,
        dimensions,
        batch_size,
    )
    return EmbeddingService(embedder=embedder, model=embedding_model, dimensions=dimensions)
