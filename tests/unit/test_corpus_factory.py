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

"""Coverage for :func:`build_corpus_context`.

The factory keeps BM25 on Postgres (:class:`PostgresCorpus`, lexical half) and
routes the dense half to the backend named by ``FLYCANON_VECTOR_STORE``:
``pgvector`` (RLS-scoped, the default), ``qdrant``, or ``chroma``. Every dense
backend is wrapped in :class:`TenantScopedVectorStore` so reads/writes are
confined to ``(tenant_id, workspace_id)``. Unknown backends fail loud.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fireflyframework_agentic.vectorstores import TenantScopedVectorStore

from flycanon.core.services.retrieval import corpus_factory


def _settings(**overrides) -> object:
    base: dict = {
        "vector_store": "pgvector",
        "embedding_dimensions": 1536,
        "database_url": "postgresql+asyncpg://canon:canon@localhost:5432/flycanon",
        "pgvector_table": "canon_chunk_vectors",
        "pgvector_hnsw_m": 16,
        "pgvector_hnsw_ef_construction": 64,
        "pgvector_hnsw_ef_search": 200,
        "bm25_text_search_config": "simple",
        "qdrant_url": "http://localhost:6333",
        "qdrant_api_key": None,
        "qdrant_collection": "canon_vectors",
        "chroma_host": "",
        "chroma_port": 8000,
        "chroma_collection": "canon_vectors",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestBackendSelection:
    def test_pgvector_routes_to_rls_store_wrapped_in_scope(self):
        settings = _settings()
        with (
            patch("flycanon.core.services.retrieval.postgres_corpus.PostgresCorpus") as corpus_mock,
            patch("flycanon.core.services.retrieval.pgvector_store.RlsPgVectorVectorStore") as store_mock,
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "pgvector"
        corpus_mock.assert_called_once_with(
            database_url=settings.database_url,
            search_config=settings.bm25_text_search_config,
        )
        store_mock.assert_called_once_with(
            database_url=settings.database_url,
            dimension=settings.embedding_dimensions,
            table_name=settings.pgvector_table,
            hnsw_m=settings.pgvector_hnsw_m,
            hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
            hnsw_ef_search=settings.pgvector_hnsw_ef_search,
        )
        assert isinstance(ctx.vector_store, TenantScopedVectorStore)
        assert ctx.vector_store._inner is store_mock.return_value

    def test_default_backend_is_pgvector(self):
        settings = _settings(vector_store="")
        with (
            patch("flycanon.core.services.retrieval.postgres_corpus.PostgresCorpus"),
            patch("flycanon.core.services.retrieval.pgvector_store.RlsPgVectorVectorStore"),
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "pgvector"

    def test_qdrant_routes_to_qdrant_store_wrapped_in_scope(self):
        settings = _settings(vector_store="qdrant")
        with (
            patch("flycanon.core.services.retrieval.postgres_corpus.PostgresCorpus"),
            patch("fireflyframework_agentic.vectorstores.QdrantVectorStore") as store_mock,
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "qdrant"
        store_mock.assert_called_once_with(
            collection_name=settings.qdrant_collection,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            vector_size=settings.embedding_dimensions,
        )
        assert isinstance(ctx.vector_store, TenantScopedVectorStore)
        assert ctx.vector_store._inner is store_mock.return_value

    def test_chroma_routes_to_chroma_store_wrapped_in_scope(self):
        settings = _settings(vector_store="chroma")
        with (
            patch("flycanon.core.services.retrieval.postgres_corpus.PostgresCorpus"),
            patch("fireflyframework_agentic.vectorstores.ChromaVectorStore") as store_mock,
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "chroma"
        # No chroma_host configured -> ephemeral in-process client (client=None).
        store_mock.assert_called_once_with(collection_name=settings.chroma_collection, client=None)
        assert isinstance(ctx.vector_store, TenantScopedVectorStore)

    @pytest.mark.parametrize("backend", ["sqlite-vec", "memory", "pinecone", "weaviate", "bogus"])
    def test_unsupported_backends_raise(self, backend: str):
        settings = _settings(vector_store=backend)
        with pytest.raises(ValueError, match="unsupported FLYCANON_VECTOR_STORE"):
            corpus_factory.build_corpus_context(settings=settings)
