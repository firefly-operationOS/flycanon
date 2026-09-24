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

"""Embedding service.

Thin wrapper around the embedder family shipped by
``fireflyframework_agentic.embeddings``. The service hides the
provider-selection logic from upstream callers so:

* the ingestion stage just hands it ``list[str]`` and gets
  ``list[list[float]]`` back,
* the query stage uses the same instance to embed the query string,
* swapping the provider (OpenAI -> Azure -> Cohere -> Bedrock -> ...) is
  a one-line settings change plus a ``flycanon reindex``, never a fresh
  database -- which embedding SETS are what make true.
"""

from __future__ import annotations

from flycanon.core.services.embeddings.embedding_service import (
    EmbeddingError,
    EmbeddingRegistry,
    EmbeddingService,
    EmbeddingThrottled,
    build_embedding_service,
)
from flycanon.core.services.embeddings.embedding_sets import (
    EmbeddingSetBinding,
    EmbeddingSetError,
    EmbeddingSetService,
    bind_embedding_set,
    current_embedding_set,
    split_embedding_model,
)

__all__ = [
    "EmbeddingError",
    "EmbeddingRegistry",
    "EmbeddingService",
    "EmbeddingSetBinding",
    "EmbeddingSetError",
    "EmbeddingSetService",
    "EmbeddingThrottled",
    "bind_embedding_set",
    "build_embedding_service",
    "current_embedding_set",
    "split_embedding_model",
]
