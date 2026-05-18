# Copyright 2026 Firefly Software Solutions Inc
"""Embedding orchestration.

Resolves a ``<provider>:<model>`` identifier to the concrete embedder
implementation shipped by ``fireflyframework_agentic.embeddings`` and
exposes a single :meth:`embed` method the ingestion / query stages
call.

Provider selection is deliberately narrow at this stage -- only the
embedders the corpus-search extra installs (OpenAI, Cohere, Google,
Mistral, Voyage, Azure OpenAI, Bedrock, Ollama) are wired. Adding a
new provider is a two-line change: import the class, add a branch in
:func:`build_embedding_service`.
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

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` in one call. Returns one vector per input.

        The underlying embedder handles batching; we still split
        absurdly large inputs into 64-element windows so memory use
        stays bounded.
        """
        if not texts:
            return []
        items = list(texts)
        vectors: list[list[float]] = []
        window = 64
        for start in range(0, len(items), window):
            chunk = items[start : start + window]
            try:
                result = await self._embedder.embed(chunk)  # type: ignore[attr-defined]
            except Exception as exc:
                raise EmbeddingError(f"embedding call failed: {exc}") from exc
            chunk_vectors = getattr(result, "embeddings", None)
            if chunk_vectors is None:
                raise EmbeddingError("embedder result missing ``embeddings`` attribute")
            vectors.extend(list(v) for v in chunk_vectors)
        if len(vectors) != len(items):
            raise EmbeddingError(
                f"embedder returned {len(vectors)} vectors for {len(items)} inputs"
            )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


def _build_embedder(*, provider: str, model: str, dimensions: int, batch_size: int) -> object:
    """Pick the concrete embedder for ``provider``.

    Heavy imports are deferred so a configuration that never touches a
    given provider doesn't pull its SDK off the wheel.
    """
    p = provider.strip().lower()
    if p == "openai":
        from fireflyframework_agentic.embeddings.providers.openai_embedder import OpenAIEmbedder

        return OpenAIEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "azure" or p == "azure-openai":
        from fireflyframework_agentic.embeddings.providers.azure_openai_embedder import (
            AzureOpenAIEmbedder,
        )

        return AzureOpenAIEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "cohere":
        from fireflyframework_agentic.embeddings.providers.cohere_embedder import CohereEmbedder

        return CohereEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "google" or p == "gemini":
        from fireflyframework_agentic.embeddings.providers.google_embedder import GoogleEmbedder

        return GoogleEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "mistral":
        from fireflyframework_agentic.embeddings.providers.mistral_embedder import MistralEmbedder

        return MistralEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "voyage":
        from fireflyframework_agentic.embeddings.providers.voyage_embedder import VoyageEmbedder

        return VoyageEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "bedrock":
        from fireflyframework_agentic.embeddings.providers.bedrock_embedder import BedrockEmbedder

        return BedrockEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "ollama":
        from fireflyframework_agentic.embeddings.providers.ollama_embedder import OllamaEmbedder

        return OllamaEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
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
            "FLYCANON_EMBEDDING_MODEL must be ``<provider>:<model>`` "
            f"(got {embedding_model!r})"
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
