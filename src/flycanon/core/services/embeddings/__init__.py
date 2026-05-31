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
