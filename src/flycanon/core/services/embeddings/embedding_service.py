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
implementation shipped by ``fireflyframework_agentic.embeddings`` and exposes a
single :meth:`EmbeddingService.embed` the ingestion, query and reindex stages
call.

Every provider the framework ships is wired -- OpenAI, Azure OpenAI, Cohere,
Google, Mistral, Voyage, Bedrock, Ollama -- and none of them is privileged.
Adding one is an import and a branch in :func:`_build_embedder`.

One process, several embedders
-------------------------------
:class:`EmbeddingRegistry` caches one :class:`EmbeddingService` per
``(provider, model, dimensions)``. Before 26.8.0 the service was a single
process-wide bean built from ``FLYCANON_EMBEDDING_MODEL``, which made two
things impossible that a shared deployment needs: serving a workspace whose
corpus was embedded by a different model than this process is configured for
(the dual-set window of a re-embed), and letting two tenants of one shared
flycanon sit on different embedders at all. The registry makes both fall out:
the caller resolves the workspace's embedding set and asks for the embedder
that matches it. The process default is pre-built at boot so the common
single-embedder deployment pays nothing for the generality.

Two failure behaviours worth knowing about
-------------------------------------------
**Zero vectors are opt-in now.** ``_embed_one_by_one`` used to catch any
per-item failure and append ``[0.0] * dimensions`` with a warning, so a
provider blip wrote rows into the ANN index that were indistinguishable from
real ones and unrecoverable afterwards. That is a data-integrity bug wherever
it fires, so it is off by default (``FLYCANON_EMBEDDING_ZERO_VECTOR_ON_FAILURE``)
and the reindex path refuses it outright, whatever the setting says.

**A 429 is a throttle, not an error.** Azure quota is per deployment in
TPM/RPM and a 429 carries ``Retry-After``. :meth:`EmbeddingService.embed`
honours it exactly, with jitter, and halves its in-flight window; a run that
is still being throttled after :data:`_MAX_THROTTLE_RETRIES` raises
:class:`EmbeddingThrottled` rather than a generic failure, so a long re-embed
can record "throttled", keep its cursor and not spend one of its three
attempts on a rate limit.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from collections.abc import Sequence

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetBinding

logger = logging.getLogger(__name__)

#: How many times one batch is re-offered after a documented throttle before
#: the caller is told to stop and resume later.
_MAX_THROTTLE_RETRIES = 5
#: Cap on an honoured ``Retry-After``. A provider asking for longer than this
#: is asking for a maintenance window, not a sleep.
_MAX_RETRY_AFTER_S = 300.0


class EmbeddingError(Exception):
    """Wraps every embedder failure so callers don't depend on the agentic
    framework's exception tree."""


class EmbeddingThrottled(EmbeddingError):
    """The provider is rate-limiting and asked us to come back later."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def retry_after_seconds(exc: BaseException) -> float | None:
    """Extract a ``Retry-After`` from whatever shape the provider raised.

    Returns ``None`` when the exception is not a documented throttle, which is
    also how callers tell a rate limit from a real error. Every provider SDK
    spells this differently and some wrap the original exception, so the
    response is looked for on the exception, on its ``response`` attribute and
    on its ``__cause__`` chain rather than assumed.
    """
    seen: set[int] = set()
    candidate: BaseException | None = exc
    while candidate is not None and id(candidate) not in seen:
        seen.add(id(candidate))
        response = getattr(candidate, "response", None)
        status = getattr(response, "status_code", None) or getattr(candidate, "status_code", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            raw = None
            try:
                raw = headers.get("retry-after") or headers.get("Retry-After")
            except AttributeError:  # pragma: no cover - a headers object without .get
                raw = None
            if raw is not None:
                try:
                    return max(0.0, min(float(raw), _MAX_RETRY_AFTER_S))
                except (TypeError, ValueError):
                    # An HTTP-date Retry-After. Honour the throttle without
                    # trying to parse a clock we do not trust.
                    return 30.0
        if status == 429:
            # A 429 with no header still means "slow down".
            return 5.0
        if type(candidate).__name__ in ("RateLimitError", "TooManyRequests"):
            return 5.0
        candidate = candidate.__cause__ or candidate.__context__
    return None


class EmbeddingService:
    """Bounded surface over ``fireflyframework_agentic`` embedders."""

    def __init__(
        self,
        *,
        embedder: object,
        model: str,
        dimensions: int,
        zero_vector_on_failure: bool = False,
        max_concurrency: int = 64,
    ) -> None:
        self._embedder = embedder
        self._model = model
        self._dimensions = dimensions
        self._zero_vector_on_failure = zero_vector_on_failure
        # The in-flight window, and the knob adaptive throttling turns down.
        self._max_window = max(1, int(max_concurrency))

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def zero_vector_on_failure(self) -> bool:
        return self._zero_vector_on_failure

    def strict(self) -> EmbeddingService:
        """A view of this service that never fabricates a vector.

        The reindex path uses it whatever the deployment's setting says: a
        zero vector written during a re-embed is a permanently wrong row in
        the set that is about to start answering queries.
        """
        if not self._zero_vector_on_failure:
            return self
        return EmbeddingService(
            embedder=self._embedder,
            model=self._model,
            dimensions=self._dimensions,
            zero_vector_on_failure=False,
            max_concurrency=self._max_window,
        )

    # Most embedders have a hard token / character limit per input
    # (nomic-embed-text: ~8 192 tokens, OpenAI text-embedding-3-*:
    # 8 191 tokens, Voyage / Cohere: 8 000 tokens). 8 000 characters
    # is a conservative cap that keeps every supported provider happy
    # without truncating typical chunked content.
    _MAX_INPUT_CHARS = 8000

    @property
    def max_input_chars(self) -> int:
        """The per-input truncation. The reindex estimate needs it.

        A cost estimate that ignores the truncation overstates the bill, and a
        corpus with a few very long chunks overstates it by a lot.
        """
        return self._MAX_INPUT_CHARS

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` in one call. Returns one vector per input.

        The underlying embedder handles batching; we still split absurdly
        large inputs into windows so memory use stays bounded, and the window
        halves under a documented throttle and recovers slowly. Each input is
        truncated to :attr:`_MAX_INPUT_CHARS` so a single oversized chunk
        doesn't fail the whole batch with a 400 from the provider.
        """
        if not texts:
            return []
        items = [self._truncate(t) for t in texts]
        vectors: list[list[float]] = []
        window = self._max_window
        start = 0
        while start < len(items):
            batch = items[start : start + window]
            batch_vectors, window = await self._embed_window(batch, window)
            vectors.extend(batch_vectors)
            start += len(batch)
        if len(vectors) != len(items):
            raise EmbeddingError(f"embedder returned {len(vectors)} vectors for {len(items)} inputs")
        return vectors

    async def _embed_window(self, batch: list[str], window: int) -> tuple[list[list[float]], int]:
        """One window, honouring ``Retry-After`` and adapting the window size.

        Returns the vectors and the window to use for the next one -- halved
        while the provider is throttling, nudged back up when it is not, so a
        long run finds the rate the deployment's quota actually allows instead
        of hammering it at a fixed concurrency.
        """
        for attempt in range(_MAX_THROTTLE_RETRIES + 1):
            try:
                return await self._embed_batch(batch), min(self._max_window, window + max(1, window // 4))
            except Exception as exc:
                delay = retry_after_seconds(exc)
                if delay is None:
                    logger.warning(
                        "embedding batch failed (%s); falling back to one-by-one",
                        exc,
                    )
                    return await self._embed_one_by_one(batch), window
                if attempt == _MAX_THROTTLE_RETRIES:
                    raise EmbeddingThrottled(
                        f"{self._model} is rate-limiting after {_MAX_THROTTLE_RETRIES} honoured "
                        f"Retry-After waits; the last asked for {delay:.1f}s. Nothing was written.",
                        retry_after=delay,
                    ) from exc
                window = max(1, window // 2)
                # Jitter so a fleet of workers does not re-offer in lockstep.
                sleep_for = delay + random.uniform(0.0, min(1.0, delay * 0.1))
                logger.info(
                    "%s throttled; honouring Retry-After %.1fs (sleeping %.1fs), window -> %d",
                    self._model,
                    delay,
                    sleep_for,
                    window,
                )
                await asyncio.sleep(sleep_for)
        raise EmbeddingThrottled(f"{self._model} is rate-limiting")  # pragma: no cover - loop exhausts above

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        result = await self._embedder.embed(batch)  # type: ignore[attr-defined]
        chunk_vectors = getattr(result, "embeddings", None)
        if chunk_vectors is None:
            raise EmbeddingError("embedder result missing ``embeddings`` attribute")
        return [list(v) for v in chunk_vectors]

    async def _embed_one_by_one(self, batch: list[str]) -> list[list[float]]:
        """Retry a failed batch item by item.

        A single item that still fails FAILS THE CALL unless the deployment
        has explicitly opted into zero vectors. Before 26.8.0 it appended
        ``[0.0] * dimensions`` and carried on, so the corpus silently acquired
        rows that the ANN index ranks as equidistant from everything and that
        nothing marks as broken.
        """
        out: list[list[float]] = []
        for idx, text in enumerate(batch):
            try:
                result = await self._embedder.embed([text])  # type: ignore[attr-defined]
                vec = getattr(result, "embeddings", None)
                if not vec:
                    raise EmbeddingError("empty embedding response")
                out.append(list(vec[0]))
            except Exception as exc:
                if not self._zero_vector_on_failure:
                    raise EmbeddingError(
                        f"embedding failed for item {idx} ({len(text)} chars) with {self._model}: {exc}. "
                        "Nothing was written. Set FLYCANON_EMBEDDING_ZERO_VECTOR_ON_FAILURE=true to "
                        "index a zero vector instead -- an unrecoverable, unmarked wrong row -- and "
                        "keep the ingest going."
                    ) from exc
                logger.warning(
                    "embedding failed for item %d (%d chars): %s; using zero vector "
                    "(FLYCANON_EMBEDDING_ZERO_VECTOR_ON_FAILURE=true)",
                    idx,
                    len(text),
                    exc,
                )
                out.append([0.0] * self._dimensions)
        return out

    def _truncate(self, text: str) -> str:
        s = text or ""
        if len(s) <= self._MAX_INPUT_CHARS:
            return s or " "  # provider rejects empty string
        return s[: self._MAX_INPUT_CHARS]

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


def _build_embedder(
    *,
    provider: str,
    model: str,
    dimensions: int,
    batch_size: int,
    settings: CanonSettings | None = None,
) -> object:
    """Pick the concrete embedder for ``provider``.

    Heavy imports are deferred so a configuration that never touches a given
    provider doesn't pull its SDK off the wheel. Module names follow
    ``fireflyframework_agentic.embeddings.providers.<provider>`` (one module
    per provider, no ``_embedder`` suffix).
    """
    import os

    p = provider.strip().lower()
    if p == "openai":
        from fireflyframework_agentic.embeddings.providers.openai import OpenAIEmbedder

        return OpenAIEmbedder(model=model, dimensions=dimensions, batch_size=batch_size)
    if p == "azure" or p == "azure-openai":
        from flycanon.core.services.embeddings.azure import (
            FlycanonAzureEmbedder,
            entra_token_provider,
            require_azure_configuration,
        )

        azure = settings or CanonSettings()
        require_azure_configuration(
            endpoint=azure.azure_openai_endpoint,
            api_version=azure.azure_openai_api_version,
            api_key=azure.azure_openai_api_key,
            auth=azure.azure_auth,
        )
        managed = azure.azure_auth == "managed_identity"
        return FlycanonAzureEmbedder(
            model,
            dimensions,
            batch_size=batch_size,
            azure_endpoint=azure.azure_openai_endpoint,
            api_version=azure.azure_openai_api_version,
            api_key=None if managed else azure.azure_openai_api_key,
            token_provider=entra_token_provider() if managed else None,
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
    settings: CanonSettings | None = None,
    zero_vector_on_failure: bool = False,
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
        settings=settings,
    )
    logger.info(
        "embedding service ready provider=%s model=%s dimensions=%d batch_size=%d zero_vector=%s",
        provider,
        model,
        dimensions,
        batch_size,
        zero_vector_on_failure,
    )
    return EmbeddingService(
        embedder=embedder,
        model=embedding_model,
        dimensions=dimensions,
        zero_vector_on_failure=zero_vector_on_failure,
        max_concurrency=batch_size,
    )


class EmbeddingRegistry:
    """One :class:`EmbeddingService` per ``(provider, model, dimensions)``.

    Lazily built and cached for the process lifetime. The cache is keyed by
    the configuration rather than by the embedding-set id, so two workspaces
    on equivalent sets share one client and one connection pool.
    """

    def __init__(self, *, settings: CanonSettings) -> None:
        self._settings = settings
        self._services: dict[tuple[str, str, int], EmbeddingService] = {}
        self._lock = threading.Lock()
        # The process default, built eagerly so a misconfigured
        # FLYCANON_EMBEDDING_MODEL fails at boot rather than on first ingest.
        provider, _, model = settings.embedding_model.partition(":")
        self._default_key = (provider.strip().lower(), model.strip(), settings.embedding_dimensions)
        self._services[self._default_key] = build_embedding_service(
            embedding_model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            settings=settings,
            zero_vector_on_failure=settings.embedding_zero_vector_on_failure,
        )

    @property
    def default(self) -> EmbeddingService:
        """The embedder ``FLYCANON_EMBEDDING_MODEL`` names."""
        return self._services[self._default_key]

    def for_model(self, *, provider: str, model: str, dimensions: int) -> EmbeddingService:
        key = (provider.strip().lower(), model.strip(), int(dimensions))
        cached = self._services.get(key)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._services.get(key)
            if cached is not None:
                return cached
            service = build_embedding_service(
                embedding_model=f"{key[0]}:{key[1]}",
                dimensions=key[2],
                batch_size=self._settings.embedding_batch_size,
                settings=self._settings,
                zero_vector_on_failure=self._settings.embedding_zero_vector_on_failure,
            )
            self._services[key] = service
            return service

    def for_binding(self, binding: EmbeddingSetBinding) -> EmbeddingService:
        """The embedder that produced -- and can query -- one embedding set."""
        return self.for_model(provider=binding.provider, model=binding.model, dimensions=binding.dimensions)
