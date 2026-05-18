# Copyright 2026 Firefly Software Solutions Inc
"""Factory for the SQLite-backed corpus + vector store.

Both halves of the retrieval index live in the same SQLite file by
default. The agentic framework's ``SqliteCorpus`` owns the FTS5 BM25
table; ``SqliteVecVectorStore`` co-resides in the same database
file and owns the ``vec_chunks`` virtual table. Co-residence keeps
deployment single-node-friendly and avoids the two-files-out-of-sync
class of bug.

For production deployments that outgrow a single SQLite file, set
``FLYCANON_VECTOR_STORE=pgvector`` and the framework's
``PgVectorVectorStore`` is wired instead. That path leaves the corpus
schema unchanged -- only the vector projection moves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CorpusContext:
    """Lifecycle handles for the retrieval index.

    ``corpus`` is the BM25 / chunk store; ``vector_store`` is the
    dense-vector projection. Both are wired into the agentic
    :class:`HybridRetriever` at query time.
    """

    corpus: object
    vector_store: object
    path: Path

    async def initialise(self) -> None:
        """Open the SQLite file and create every required schema.

        Idempotent -- safe to call on every cold start.
        """
        await self.corpus.initialise()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self.corpus.close()  # type: ignore[attr-defined]
        close = getattr(self.vector_store, "close", None)
        if callable(close):
            await close()


def build_corpus_context(
    *,
    backend: str,
    corpus_path: str,
    dimensions: int,
) -> CorpusContext:
    """Resolve the configured backend into a :class:`CorpusContext`.

    ``sqlite-vec`` (default) shares one SQLite file with the corpus.
    ``pgvector`` swaps the vector half for the agentic
    ``PgVectorVectorStore`` and leaves the BM25 corpus on SQLite --
    BM25 over Postgres is a future enhancement (we'd need to
    materialise a ``tsvector`` projection of every chunk on commit).
    """
    from fireflyframework_agentic.rag.corpus import SqliteCorpus

    path = Path(corpus_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    corpus = SqliteCorpus(path)

    b = backend.strip().lower()
    if b in {"sqlite-vec", "sqlite_vec", "sqlite"}:
        from fireflyframework_agentic.vectorstores.sqlite_vec_store import SqliteVecVectorStore

        vector_store = SqliteVecVectorStore(path, dimension=dimensions)
        logger.info("retrieval index ready backend=sqlite-vec path=%s dim=%d", path, dimensions)
        return CorpusContext(corpus=corpus, vector_store=vector_store, path=path)

    if b == "pgvector":
        # Lazy import: pgvector is an optional extra.
        try:
            from fireflyframework_agentic.vectorstores.pgvector_store import PgVectorVectorStore
        except ImportError as exc:  # pragma: no cover - extra path
            raise RuntimeError(
                "pgvector backend requires the ``pgvector`` extra; "
                "install with ``uv sync --extra pgvector``."
            ) from exc

        # The Postgres URL is read from the FLYCANON_DATABASE_URL env var
        # by the agentic store -- nothing else to do here.
        vector_store = PgVectorVectorStore(dimension=dimensions)
        logger.info("retrieval index ready backend=pgvector dim=%d", dimensions)
        return CorpusContext(corpus=corpus, vector_store=vector_store, path=path)

    raise ValueError(
        f"unknown FLYCANON_VECTOR_STORE={backend!r}; expected 'sqlite-vec' or 'pgvector'"
    )
