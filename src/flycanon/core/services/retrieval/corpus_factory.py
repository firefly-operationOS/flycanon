# Copyright 2026 Firefly Software Solutions Inc
"""Factory for the BM25 corpus + dense-vector projection.

flycanon does hybrid retrieval in two halves fused by Reciprocal Rank Fusion.
The **lexical** half (BM25 over the GENERATED ``tsv`` column of ``canon_chunks``,
:class:`PostgresCorpus`) always rides on the canonical Postgres instance. The
**dense** half is pluggable, selected by ``FLYCANON_VECTOR_STORE``:

* ``pgvector`` (default) -- :class:`RlsPgVectorVectorStore`, co-located with the
  canonical Postgres and protected by namespace-keyed Row-Level Security.
* ``qdrant`` -- the framework's :class:`QdrantVectorStore`.
* ``chroma`` -- the framework's :class:`ChromaVectorStore`.

Whatever the backend, the dense store is wrapped in
:class:`~fireflyframework_agentic.vectorstores.TenantScopedVectorStore` so every
read/write/delete is confined to ``(tenant_id, workspace_id)`` via the canonical
``t/<tenant>/w/<workspace>`` namespace -- the explicit, fail-loud scope contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fireflyframework_agentic.vectorstores import TenantScopedVectorStore
from fireflyframework_agentic.vectorstores.scoped import ScopedVectorStore

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)

# Dense backends flycanon can route to. Lexical/BM25 always stays on Postgres.
SUPPORTED_BACKENDS = ("pgvector", "qdrant", "chroma")


@dataclass(slots=True)
class CorpusContext:
    """Lifecycle handles for the retrieval index."""

    corpus: object
    vector_store: ScopedVectorStore
    backend: str

    async def initialise(self) -> None:
        """Open the corpus + dense store and create every required schema.

        Idempotent. Both halves are initialised eagerly so a misconfigured
        backend fails at boot rather than on the first request.
        """
        await self.corpus.initialise()  # type: ignore[attr-defined]
        await self.vector_store.initialise()

    async def close(self) -> None:
        await self.corpus.close()  # type: ignore[attr-defined]
        await self.vector_store.close()


def build_corpus_context(*, settings: CanonSettings) -> CorpusContext:
    """Resolve ``FLYCANON_VECTOR_STORE`` into a populated context."""
    backend = (settings.vector_store or "pgvector").strip().lower()

    from flycanon.core.services.retrieval.postgres_corpus import PostgresCorpus

    corpus = PostgresCorpus(
        database_url=settings.database_url,
        search_config=settings.bm25_text_search_config,
    )
    vector_store = TenantScopedVectorStore(_build_dense_store(backend, settings))
    logger.info(
        "retrieval index ready backend=%s dim=%d",
        backend,
        settings.embedding_dimensions,
    )
    return CorpusContext(corpus=corpus, vector_store=vector_store, backend=backend)


def _build_dense_store(backend: str, settings: CanonSettings):
    """Construct the (unscoped) dense backend named by *backend*.

    Backend client libraries are imported lazily so only the selected backend's
    optional dependency needs to be installed.
    """
    if backend == "pgvector":
        from flycanon.core.services.retrieval.pgvector_store import RlsPgVectorVectorStore

        return RlsPgVectorVectorStore(
            database_url=settings.database_url,
            dimension=settings.embedding_dimensions,
            table_name=settings.pgvector_table,
            hnsw_m=settings.pgvector_hnsw_m,
            hnsw_ef_construction=settings.pgvector_hnsw_ef_construction,
            hnsw_ef_search=settings.pgvector_hnsw_ef_search,
        )

    if backend == "qdrant":
        from fireflyframework_agentic.vectorstores import QdrantVectorStore

        return QdrantVectorStore(
            collection_name=settings.qdrant_collection,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            vector_size=settings.embedding_dimensions,
        )

    if backend == "chroma":
        from fireflyframework_agentic.vectorstores import ChromaVectorStore

        return ChromaVectorStore(collection_name=settings.chroma_collection, client=_chroma_client(settings))

    raise ValueError(
        f"unsupported FLYCANON_VECTOR_STORE={backend!r}; supported backends: {', '.join(SUPPORTED_BACKENDS)}."
    )


def _chroma_client(settings: CanonSettings):
    """Build a Chroma HTTP client when a host is configured, else ephemeral.

    Returns ``None`` to let :class:`ChromaVectorStore` create an in-process
    ephemeral client (dev/test); a real deployment sets ``FLYCANON_CHROMA_HOST``.
    """
    if not settings.chroma_host:
        return None
    import chromadb

    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
