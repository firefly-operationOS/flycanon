# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :func:`build_corpus_context`.

The factory is the only seam through which a vector backend is
chosen, so we want it well-protected: every supported value of
``FLYCANON_VECTOR_STORE`` lands at the right adapter class (with a
ditto for aliases), unknown values raise, and optional backends
report the right install hint when the adapter isn't on the
classpath.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.retrieval import corpus_factory


def _settings(**overrides) -> CanonSettings:
    base: dict = {
        "vector_store": "sqlite-vec",
        "corpus_path": "/tmp/flycanon-test-corpus.sqlite",
        "embedding_dimensions": 1536,
        "database_url": "postgresql+asyncpg://canon:canon@localhost:5432/flycanon",
        "pgvector_table": "canon_embeddings",
        "pgvector_hnsw_m": 16,
        "pgvector_hnsw_ef_construction": 64,
        "chroma_collection": "flycanon",
        "qdrant_url": "http://qdrant:6333",
        "qdrant_collection": "flycanon",
        "qdrant_api_key": "",
        "pinecone_index": "flycanon",
        "pinecone_api_key": "",
        "bm25_text_search_config": "simple",
    }
    base.update(overrides)
    return SimpleNamespace(**base)  # type: ignore[return-value]


class TestBackendSelection:
    def test_sqlite_vec_default_routes_to_sqlite_vec_store(self, tmp_path: Path):
        settings = _settings(corpus_path=str(tmp_path / "corpus.sqlite"))
        with (
            patch("fireflyframework_agentic.rag.corpus.SqliteCorpus") as corpus_mock,
            patch("fireflyframework_agentic.vectorstores.sqlite_vec_store.SqliteVecVectorStore") as vec_mock,
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "sqlite-vec"
        corpus_mock.assert_called_once()
        vec_mock.assert_called_once()

    @pytest.mark.parametrize("alias", ["sqlite-vec", "sqlite_vec", "sqlite", "SQLITE-VEC"])
    def test_sqlite_vec_aliases_all_route_to_sqlite_vec(self, tmp_path: Path, alias: str):
        settings = _settings(vector_store=alias, corpus_path=str(tmp_path / "corpus.sqlite"))
        with (
            patch("fireflyframework_agentic.rag.corpus.SqliteCorpus"),
            patch("fireflyframework_agentic.vectorstores.sqlite_vec_store.SqliteVecVectorStore") as vec_mock,
        ):
            corpus_factory.build_corpus_context(settings=settings)
        vec_mock.assert_called_once()

    def test_pgvector_routes_to_pgvector_store(self, tmp_path: Path):
        settings = _settings(vector_store="pgvector", corpus_path=str(tmp_path / "corpus.sqlite"))
        with (
            patch("flycanon.core.services.retrieval.postgres_corpus.PostgresCorpus") as pg_corpus_mock,
            patch("flycanon.core.services.retrieval.pgvector_store.PgVectorVectorStore") as pgvec_mock,
        ):
            ctx = corpus_factory.build_corpus_context(settings=settings)
        assert ctx.backend == "pgvector"
        # The pgvector path uses the Postgres-native BM25 corpus
        # (tsvector + GIN on canon_chunks) instead of the file-backed
        # SQLite FTS5 corpus -- co-located with the dense projection.
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

    def test_memory_backend_routes_to_in_memory_store(self, tmp_path: Path):
        settings = _settings(vector_store="memory", corpus_path=str(tmp_path / "corpus.sqlite"))
        with (
            patch("fireflyframework_agentic.rag.corpus.SqliteCorpus"),
            patch("fireflyframework_agentic.vectorstores.memory_store.InMemoryVectorStore") as mem_mock,
        ):
            corpus_factory.build_corpus_context(settings=settings)
        mem_mock.assert_called_once_with()

    def test_unknown_backend_raises_value_error(self, tmp_path: Path):
        settings = _settings(vector_store="weaviate", corpus_path=str(tmp_path / "corpus.sqlite"))
        with (
            patch("fireflyframework_agentic.rag.corpus.SqliteCorpus"),
            pytest.raises(ValueError, match="unknown FLYCANON_VECTOR_STORE"),
        ):
            corpus_factory.build_corpus_context(settings=settings)
