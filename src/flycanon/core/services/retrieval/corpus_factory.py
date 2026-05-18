# Copyright 2026 Firefly Software Solutions Inc
"""Factory for the BM25 corpus + dense-vector projection.

Backend matrix (settings-driven, all first-class):

* ``sqlite-vec``  -- single-file SQLite (FTS5 + sqlite-vec). Best for
                     single-node deployments and the default in
                     local dev.
* ``pgvector``    -- pgvector co-located with the canonical Postgres
                     instance (HNSW index over cosine distance).
                     Production-grade; no extra service to operate.
* ``chroma``      -- ChromaDB cluster (managed or self-hosted).
                     Good for teams already running Chroma.
* ``qdrant``      -- Qdrant (self-hosted or Cloud). Best for hybrid
                     workloads that already use Qdrant for other
                     retrieval surfaces.
* ``pinecone``    -- Pinecone Serverless. Fully managed; lowest
                     ops burden.
* ``memory``      -- In-memory, ephemeral. Used by the test suite.

The BM25 layer stays on SQLite regardless of the vector backend --
the agentic ``SqliteCorpus`` is a file-based FTS5 index (not the
source of truth, just a search projection). For production
deployments the file can sit on a fast persistent volume; the
canonical chunk rows live in Postgres and are the replay anchor if
the corpus file ever needs to be rebuilt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CorpusContext:
    """Lifecycle handles for the retrieval index."""

    corpus: object
    vector_store: object
    backend: str
    path: Path | None = None

    async def initialise(self) -> None:
        """Open the corpus + create every required schema. Idempotent."""
        await self.corpus.initialise()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self.corpus.close()  # type: ignore[attr-defined]
        close = getattr(self.vector_store, "close", None)
        if callable(close):
            await close()


def build_corpus_context(
    *,
    settings: CanonSettings,
) -> CorpusContext:
    """Resolve ``FLYCANON_VECTOR_STORE`` into a populated context.

    BM25 corpus selection is co-located with the vector store: when
    Postgres is the dense store (``pgvector``, default) the BM25
    projection rides on the same Postgres via the GENERATED ``tsv``
    column on ``canon_chunks`` (see migration ``0003_bm25_tsv``).
    SQLite-backed deployments stay on the file-backed FTS5 corpus
    shipped by ``fireflyframework-agentic``. Remote vector stores
    (chroma / qdrant / pinecone / memory) fall back to the SQLite
    BM25 corpus -- consumers that need the BM25 projection co-resident
    in their own service should switch to ``pgvector``.
    """
    backend = (settings.vector_store or "pgvector").strip().lower()
    corpus_path = Path(settings.corpus_path)
    corpus_path.parent.mkdir(parents=True, exist_ok=True)

    corpus = _build_corpus(settings=settings, backend=backend, corpus_path=corpus_path)
    vector_store = _build_vector_store(settings=settings, backend=backend)

    logger.info(
        "retrieval index ready backend=%s corpus=%s dim=%d",
        backend,
        type(corpus).__name__,
        settings.embedding_dimensions,
    )
    return CorpusContext(
        corpus=corpus,
        vector_store=vector_store,
        backend=backend,
        path=corpus_path,
    )


def _build_corpus(
    *,
    settings: CanonSettings,
    backend: str,
    corpus_path: Path,
) -> object:
    """Pick the BM25 corpus implementation.

    ``pgvector`` -- Postgres-native ``tsvector`` + GIN on the
    canonical ``canon_chunks`` table (no extra service to operate).
    Everything else falls back to the agentic ``SqliteCorpus`` (FTS5
    on a file-backed SQLite db) for portability.
    """
    if backend == "pgvector":
        from flycanon.core.services.retrieval.postgres_corpus import PostgresCorpus

        return PostgresCorpus(
            database_url=settings.database_url,
            search_config=settings.bm25_text_search_config,
        )
    from fireflyframework_agentic.rag.corpus import SqliteCorpus

    return SqliteCorpus(corpus_path)


def _build_vector_store(*, settings: CanonSettings, backend: str) -> object:
    if backend in {"sqlite-vec", "sqlite_vec", "sqlite"}:
        from fireflyframework_agentic.vectorstores.sqlite_vec_store import SqliteVecVectorStore

        return SqliteVecVectorStore(
            settings.corpus_path,
            dimension=settings.embedding_dimensions,
        )

    if backend == "pgvector":
        from flycanon.core.services.retrieval.pgvector_store import PgVectorVectorStore

        return PgVectorVectorStore(
            database_url=settings.database_url,
            dimension=settings.embedding_dimensions,
            table_name=settings.pgvector_table,
            hnsw_m=settings.pgvector_hnsw_m,
            hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
        )

    if backend == "chroma":
        try:
            from fireflyframework_agentic.vectorstores.chroma_store import ChromaVectorStore
        except ImportError as exc:
            raise RuntimeError(
                "chroma backend requires the ``vectorstores-chroma`` extra of fireflyframework-agentic."
            ) from exc
        return ChromaVectorStore(
            collection_name=settings.chroma_collection,
        )

    if backend == "qdrant":
        try:
            from fireflyframework_agentic.vectorstores.qdrant_store import QdrantVectorStore
        except ImportError as exc:
            raise RuntimeError(
                "qdrant backend requires the ``vectorstores-qdrant`` extra of fireflyframework-agentic."
            ) from exc
        return QdrantVectorStore(
            collection_name=settings.qdrant_collection,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            vector_size=settings.embedding_dimensions,
        )

    if backend == "pinecone":
        try:
            from fireflyframework_agentic.vectorstores.pinecone_store import PineconeVectorStore
        except ImportError as exc:
            raise RuntimeError(
                "pinecone backend requires the ``vectorstores-pinecone`` extra of fireflyframework-agentic."
            ) from exc
        return PineconeVectorStore(
            index_name=settings.pinecone_index,
            api_key=settings.pinecone_api_key or None,
        )

    if backend in {"memory", "in-memory", "in_memory"}:
        from fireflyframework_agentic.vectorstores.memory_store import InMemoryVectorStore

        return InMemoryVectorStore()

    raise ValueError(
        f"unknown FLYCANON_VECTOR_STORE={backend!r}; expected one of "
        "sqlite-vec | pgvector | chroma | qdrant | pinecone | memory"
    )
