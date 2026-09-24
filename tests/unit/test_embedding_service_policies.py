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

"""What :class:`EmbeddingService` does when the provider misbehaves.

Two behaviours changed in 26.8.0 and both are data-integrity decisions:

* a per-item failure no longer fabricates a zero vector, and
* a 429 is a throttle with a honoured ``Retry-After``, not one of the job's
  three attempts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.embedding_service import (
    EmbeddingError,
    EmbeddingRegistry,
    EmbeddingService,
    EmbeddingThrottled,
    retry_after_seconds,
)


@dataclass
class _Result:
    embeddings: list[list[float]]


class _Embedder:
    """A stub embedder with programmable failures."""

    def __init__(self, *, dimensions: int = 4, fail_batches: int = 0, fail_items: set[str] | None = None):
        self.dimensions = dimensions
        self._fail_batches = fail_batches
        self._fail_items = fail_items or set()
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> _Result:
        self.calls.append(list(texts))
        if len(texts) > 1 and self._fail_batches > 0:
            self._fail_batches -= 1
            raise RuntimeError("provider said no")
        for text in texts:
            if text in self._fail_items:
                raise RuntimeError(f"cannot embed {text!r}")
        return _Result(embeddings=[[float(len(t))] * self.dimensions for t in texts])


class _Headers(dict):
    """Minimal ``httpx``-shaped headers object."""


class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = _Headers(headers or {})


class _RateLimitError(Exception):
    def __init__(self, retry_after: str | None = None) -> None:
        super().__init__("429 Too Many Requests")
        self.response = _Response(429, {"retry-after": retry_after} if retry_after else {})


class _ThrottlingEmbedder:
    """Rate-limits the first ``n`` calls, then succeeds."""

    def __init__(self, *, throttles: int, retry_after: str | None = "2") -> None:
        self._left = throttles
        self._retry_after = retry_after
        self.calls = 0

    async def embed(self, texts: list[str]) -> _Result:
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise _RateLimitError(self._retry_after)
        return _Result(embeddings=[[1.0, 2.0] for _ in texts])


def _service(embedder: object, **kwargs) -> EmbeddingService:
    return EmbeddingService(embedder=embedder, model="stub:model", dimensions=4, **kwargs)


class TestZeroVectorPolicy:
    async def test_a_failed_item_fails_the_call_by_default(self) -> None:
        """The pre-26.8.0 behaviour wrote ``[0.0] * dimensions`` and carried on.

        Those rows entered the ANN index indistinguishable from real ones,
        nothing marked them, and they were unrecoverable afterwards.
        """
        service = _service(_Embedder(fail_batches=1, fail_items={"bad"}))
        with pytest.raises(EmbeddingError) as exc:
            await service.embed(["good", "bad"])
        message = str(exc.value)
        assert "Nothing was written" in message
        assert "FLYCANON_EMBEDDING_ZERO_VECTOR_ON_FAILURE" in message

    async def test_the_old_behaviour_is_available_and_says_so(self) -> None:
        service = _service(_Embedder(fail_batches=1, fail_items={"bad"}), zero_vector_on_failure=True)
        vectors = await service.embed(["good", "bad"])
        assert vectors[1] == [0.0, 0.0, 0.0, 0.0]
        assert vectors[0] != vectors[1]

    async def test_strict_drops_the_opt_in(self) -> None:
        """The reindex path refuses zero vectors whatever the deployment says.

        A zero vector written during a re-embed is a permanently wrong row in
        the set that is about to start answering queries.
        """
        service = _service(_Embedder(fail_batches=1, fail_items={"bad"}), zero_vector_on_failure=True)
        assert service.zero_vector_on_failure is True
        assert service.strict().zero_vector_on_failure is False
        with pytest.raises(EmbeddingError):
            await service.strict().embed(["good", "bad"])

    async def test_strict_returns_self_when_already_strict(self) -> None:
        service = _service(_Embedder())
        assert service.strict() is service

    async def test_a_batch_failure_still_falls_back_one_by_one(self) -> None:
        embedder = _Embedder(fail_batches=1)
        service = _service(embedder)
        vectors = await service.embed(["a", "bb", "ccc"])
        assert len(vectors) == 3
        # One failed batch of three, then three single-item retries.
        assert [len(c) for c in embedder.calls] == [3, 1, 1, 1]


class TestRetryAfter:
    def test_reads_the_header(self) -> None:
        assert retry_after_seconds(_RateLimitError("2")) == 2.0

    def test_a_429_without_a_header_still_reads_as_a_throttle(self) -> None:
        assert retry_after_seconds(_RateLimitError(None)) == 5.0

    def test_an_http_date_is_honoured_without_being_parsed(self) -> None:
        # We do not trust a clock we did not set; 30s is the conservative wait.
        assert retry_after_seconds(_RateLimitError("Wed, 24 Sep 2026 16:00:00 GMT")) == 30.0

    def test_it_is_capped(self) -> None:
        assert retry_after_seconds(_RateLimitError("99999")) == 300.0

    def test_a_wrapped_throttle_is_still_found(self) -> None:
        """Provider SDKs wrap; the framework wraps again.

        Looking only at the top exception would read a rate limit as a generic
        failure and spend one of the job's attempts on it.
        """
        try:
            try:
                raise _RateLimitError("3")
            except _RateLimitError as inner:
                raise RuntimeError("Azure embedding failed") from inner
        except RuntimeError as outer:
            assert retry_after_seconds(outer) == 3.0

    def test_a_plain_error_is_not_a_throttle(self) -> None:
        assert retry_after_seconds(RuntimeError("boom")) is None


class TestThrottling:
    async def test_a_throttle_is_honoured_and_the_run_continues(self, monkeypatch) -> None:
        slept: list[float] = []

        async def _sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        embedder = _ThrottlingEmbedder(throttles=2, retry_after="2")
        service = EmbeddingService(embedder=embedder, model="azure:emb", dimensions=2)
        vectors = await service.embed(["a", "b"])
        assert len(vectors) == 2
        assert len(slept) == 2
        # The Retry-After is honoured exactly, plus jitter of at most 10%.
        assert all(2.0 <= s <= 2.2 for s in slept)

    async def test_a_persistent_throttle_raises_a_distinguishable_error(self, monkeypatch) -> None:
        """So a long re-embed can record ``throttled`` and keep its cursor,
        instead of spending one of ``ingest_max_attempts``."""

        async def _sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        service = EmbeddingService(
            embedder=_ThrottlingEmbedder(throttles=99, retry_after="7"),
            model="azure:emb",
            dimensions=2,
        )
        with pytest.raises(EmbeddingThrottled) as exc:
            await service.embed(["a"])
        assert exc.value.retry_after == 7.0
        assert "Nothing was written" in str(exc.value)

    async def test_the_window_halves_under_throttling(self, monkeypatch) -> None:
        """Adaptive concurrency: the run finds the rate the quota allows.

        The embedder's own window is the knob, so a deployment against a small
        TPM quota slows down instead of failing.
        """

        async def _sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        embedder = _ThrottlingEmbedder(throttles=3, retry_after="1")
        service = EmbeddingService(embedder=embedder, model="azure:emb", dimensions=2, max_concurrency=8)
        await service.embed([f"chunk-{i}" for i in range(8)])
        # Three throttles, then the eight inputs go through in one window.
        assert embedder.calls == 4


class TestRegistry:
    def test_the_process_default_is_built_eagerly(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        registry = EmbeddingRegistry(
            settings=CanonSettings(embedding_model="openai:text-embedding-3-small", embedding_dimensions=1536)
        )
        assert registry.default.model == "openai:text-embedding-3-small"
        assert registry.default.dimensions == 1536

    def test_one_service_per_configuration_and_it_is_cached(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        registry = EmbeddingRegistry(settings=CanonSettings(embedding_model="openai:a"))
        one = registry.for_model(provider="openai", model="b", dimensions=256)
        two = registry.for_model(provider="openai", model="b", dimensions=256)
        three = registry.for_model(provider="openai", model="b", dimensions=512)
        assert one is two
        assert one is not three

    def test_a_binding_resolves_to_its_own_embedder(self, monkeypatch) -> None:
        """This is what lets one process serve two tenants on two embedders,
        and what makes a query go through the model that produced the corpus
        it is searching."""
        from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetBinding

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        registry = EmbeddingRegistry(settings=CanonSettings(embedding_model="openai:text-embedding-3-small"))
        binding = EmbeddingSetBinding(
            set_id="es-1", provider="openai", model="text-embedding-3-large", dimensions=3072
        )
        service = registry.for_binding(binding)
        assert service.model == "openai:text-embedding-3-large"
        assert service.dimensions == 3072
        assert service is not registry.default
