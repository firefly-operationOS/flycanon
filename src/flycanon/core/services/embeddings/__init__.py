# Copyright 2026 Firefly Software Solutions Inc
"""Embedding service.

Thin wrapper around the embedder family shipped by
``fireflyframework_agentic.embeddings``. The service hides the
provider-selection logic from upstream callers so:

* the ingestion stage just hands it ``list[str]`` and gets
  ``list[list[float]]`` back,
* the query stage uses the same instance to embed the query string,
* swapping the provider (OpenAI -> Cohere -> Bedrock -> ...) is a
  one-line settings change.
"""

from __future__ import annotations

from flycanon.core.services.embeddings.embedding_service import (
    EmbeddingError,
    EmbeddingService,
    build_embedding_service,
)

__all__ = ["EmbeddingError", "EmbeddingService", "build_embedding_service"]
