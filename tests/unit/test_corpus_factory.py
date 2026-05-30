# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :func:`build_corpus_context`.

flycanon is pgvector-only: the factory routes
``FLYCANON_VECTOR_STORE=pgvector`` to :class:`PostgresCorpus` (BM25 over the
``tsv`` column on ``canon_chunks``) + :class:`PgVectorVectorStore` (dense),
and rejects every other backend. The SQLite-vec / Chroma / Qdrant / Pinecone
/ memory fallbacks were removed with the ``fireflyframework_agentic.rag``
dependency.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from flycanon.core.services.retrieval import corpus_factory


def _settings(**overrides) -> object:
    base: dict = {
        "vector_store": "pgvector",
        "embedding_dimensions": 1536,
        "database_url": "postgresql+asyncpg://canon:canon@localhost:5432/flycanon",
        "pgvector_table": "canon_embeddings",
        "pgvector_hnsw_m": 16,
        "pgvector_hnsw_ef_construction": 64,
        "bm25_text_search_config": "simple",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestBackendSelection:
    def test_pgvector_routes_to_postgres_corpus_and_pgvector_store(self):
        settings = _settings()
        with (
            patch("flycanon.core.services.retrieval.postgres_corpus.PostgresCorpus") as pg_corpus_mock,
            patch("flycanon.core.services.retrieval.pgvector_store.PgVectorVectorStore") as pgvec_mock,
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "pgvector"
        pg_corpus_mock.assert_called_once_with(
            database_url=settings.database_url,
            search_config=settings.bm25_text_search_config,
        )
        pgvec_mock.assert_called_once_with(
            database_url=settings.database_url,
            dimension=settings.embedding_dimensions,
            table_name=settings.pgvector_table,
            hnsw_m=settings.pgvector_hnsw_m,
            hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
        )

    @pytest.mark.parametrize(
        "backend", ["sqlite-vec", "sqlite", "memory", "chroma", "qdrant", "pinecone", "weaviate"]
    )
    def test_non_pgvector_backends_raise(self, backend: str):
        settings = _settings(vector_store=backend)
        with pytest.raises(ValueError, match="only supports 'pgvector'"):
            corpus_factory.build_corpus_context(settings=settings)
