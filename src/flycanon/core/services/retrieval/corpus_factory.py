# Copyright 2026 Firefly Software Solutions Inc
"""Factory for the BM25 corpus + dense-vector projection.

flycanon is Postgres-native. BM25 rides on the GENERATED ``tsv`` column of
``canon_chunks`` (:class:`PostgresCorpus`) and dense vectors live in pgvector
(:class:`PgVectorVectorStore`), both co-located with the canonical Postgres
instance -- no extra service to operate, and scope isolation is enforced by
Postgres RLS.

``FLYCANON_VECTOR_STORE`` is a selector whose only supported value is
``pgvector``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CorpusContext:
    """Lifecycle handles for the retrieval index."""

    corpus: object
    vector_store: object
    backend: str

    async def initialise(self) -> None:
        """Open the corpus + create every required schema. Idempotent."""
        await self.corpus.initialise()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self.corpus.close()  # type: ignore[attr-defined]
        close = getattr(self.vector_store, "close", None)
        if callable(close):
            await close()  # type: ignore[misc]


def build_corpus_context(*, settings: CanonSettings) -> CorpusContext:
    """Resolve ``FLYCANON_VECTOR_STORE`` into a populated context.

    BM25 rides on the Postgres ``tsv`` column of ``canon_chunks`` and dense
    vectors live in pgvector, both on the canonical Postgres. Only
    ``pgvector`` is supported.
    """
    backend = (settings.vector_store or "pgvector").strip().lower()
    if backend != "pgvector":
        raise ValueError(f"unsupported FLYCANON_VECTOR_STORE={backend!r}; flycanon only supports 'pgvector'.")

    from flycanon.core.services.retrieval.pgvector_store import PgVectorVectorStore
    from flycanon.core.services.retrieval.postgres_corpus import PostgresCorpus

    corpus = PostgresCorpus(
        database_url=settings.database_url,
        search_config=settings.bm25_text_search_config,
    )
    vector_store = PgVectorVectorStore(
        database_url=settings.database_url,
        dimension=settings.embedding_dimensions,
        table_name=settings.pgvector_table,
        hnsw_m=settings.pgvector_hnsw_m,
        hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
    )
    logger.info(
        "retrieval index ready backend=%s dim=%d",
        backend,
        settings.embedding_dimensions,
    )
    return CorpusContext(corpus=corpus, vector_store=vector_store, backend=backend)
