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

"""Cross-encoder reranker over fused retrieval hits.

RRF fuses BM25 + dense rankings but doesn't look at semantic
match between the user query and each chunk's text. A cross-
encoder (Cohere Rerank, Voyage Rerank, etc.) reads
``(query, chunk)`` pairs and scores them directly -- pushing
precision@5 up materially on most corpora at a ~100ms latency
cost for 10-20 candidates.

The protocol is intentionally tiny so a future ONNX-backed local
adapter slots in without touching the retrieval service.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    """Re-orders a candidate list against a query.

    Returns a NEW list ordered best-to-worst. Implementations may
    decline to act (e.g. provider unavailable, API key missing) by
    returning the input list unchanged; callers don't need to
    distinguish those from a no-op default.
    """

    async def rerank(
        self,
        *,
        query: str,
        hits: list[Any],
        top_n: int,
    ) -> list[Any]: ...


class NoOpReranker:
    """Default adapter -- preserves the fused ranking unchanged."""

    async def rerank(self, *, query: str, hits: list[Any], top_n: int) -> list[Any]:
        return hits


class CohereReranker:
    """Cohere Rerank-v3 adapter.

    Model id format: ``cohere:rerank-multilingual-v3.0`` or
    ``cohere:rerank-english-v3.0``. Reads the API key from the
    standard ``COHERE_API_KEY`` env var.
    """

    def __init__(self, model: str) -> None:
        # Strip the provider prefix the caller registers under.
        self._model = model.split(":", 1)[1] if ":" in model else model

    async def rerank(self, *, query: str, hits: list[Any], top_n: int) -> list[Any]:
        api_key = os.environ.get("COHERE_API_KEY")
        if not api_key:
            logger.debug("COHERE_API_KEY not set -- skipping rerank")
            return hits
        if not hits:
            return hits
        try:
            import httpx
        except ImportError:  # pragma: no cover -- transitive dep
            return hits

        documents = [_chunk_text(hit) for hit in hits]
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.cohere.com/v2/rerank",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cohere rerank failed; returning fused order: %s", exc)
            return hits

        order = [int(item["index"]) for item in data.get("results", [])]
        return [hits[i] for i in order]


class VoyageReranker:
    """Voyage Rerank-2 adapter.

    Model id format: ``voyageai:rerank-2``. Reads the API key from
    ``VOYAGEAI_API_KEY``.
    """

    def __init__(self, model: str) -> None:
        self._model = model.split(":", 1)[1] if ":" in model else model

    async def rerank(self, *, query: str, hits: list[Any], top_n: int) -> list[Any]:
        api_key = os.environ.get("VOYAGEAI_API_KEY") or os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            logger.debug("VOYAGEAI_API_KEY not set -- skipping rerank")
            return hits
        if not hits:
            return hits
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return hits
        documents = [_chunk_text(hit) for hit in hits]
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_k": min(top_n, len(documents)),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.voyageai.com/v1/rerank",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Voyage rerank failed; returning fused order: %s", exc)
            return hits
        order = [int(item["index"]) for item in data.get("data", [])]
        return [hits[i] for i in order]


def build_reranker(model: str) -> Reranker:
    """Resolve the configured reranker from ``FLYCANON_RERANKER_MODEL``.

    Recognises ``cohere:*`` and ``voyageai:*`` prefixes. Empty
    string returns the :class:`NoOpReranker` -- the default for
    deployments that don't want the extra LLM hop.
    """
    if not model:
        return NoOpReranker()
    provider = model.split(":", 1)[0].lower()
    if provider == "cohere":
        return CohereReranker(model)
    if provider in {"voyageai", "voyage"}:
        return VoyageReranker(model)
    logger.warning("unknown reranker provider %r -- falling back to no-op", model)
    return NoOpReranker()


def _chunk_text(hit: Any) -> str:
    """Pull the chunk text the reranker scores against the query."""
    text = getattr(hit, "content", None) or ""
    return str(text)
