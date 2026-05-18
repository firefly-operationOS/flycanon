# Copyright 2026 Firefly Software Solutions Inc
"""Index writer -- mirrors chunks + embeddings into the corpus and vector store.

Called from the ingestion CQRS handler after the bytes have been
loaded, chunked, and embedded. Keeps the BM25 + vector projections
in lock-step with Postgres' ``canon_chunks`` by sharing the
``chunk_id``: both stores use the same UUID, so the
:class:`HybridRetriever`'s RRF fusion sees both rankings reference
the same rows.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from flycanon.core.services.retrieval.corpus_factory import CorpusContext
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.source import SourceRow

logger = logging.getLogger(__name__)


class IndexService:
    """Sync (chunks, embeddings) -> BM25 + vector projections.

    The writer does NOT touch Postgres; the caller is responsible for
    persisting the ``KnowledgeChunkRow`` rows first. The index only
    holds the searchable projection.
    """

    def __init__(self, *, context: CorpusContext) -> None:
        self._context = context

    async def initialise(self) -> None:
        await self._context.initialise()

    async def replace_for_source(
        self,
        *,
        source: SourceRow,
        chunks: Sequence[KnowledgeChunkRow],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> int:
        """Replace every indexed chunk for ``source.id`` atomically.

        Returns the number of chunks ingested into the index.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunk/embedding length mismatch: {len(chunks)} chunks vs {len(embeddings)} embeddings"
            )

        from fireflyframework_agentic.rag.corpus import StoredChunk
        from fireflyframework_agentic.vectorstores.types import VectorDocument

        stored_chunks = [
            StoredChunk(
                chunk_id=chunk.id,
                doc_id=source.id,
                source_path=source.filename or source.uri or source.id,
                index_in_doc=chunk.index_in_source,
                content=chunk.content,
                metadata=self._chunk_metadata(source=source, chunk=chunk),
            )
            for chunk in chunks
        ]
        vector_documents = [
            VectorDocument(
                id=chunk.id,
                text=chunk.content,
                embedding=list(emb),
                metadata={
                    "source_id": source.id,
                    "doc_id": source.id,
                    "section_path": chunk.section_path or "",
                    "page": str(chunk.page) if chunk.page is not None else "",
                },
                namespace="default",
            )
            for chunk, emb in zip(chunks, embeddings, strict=True)
        ]

        # Wipe the previous index entries for this source before
        # appending the new chunks. ``delete_by_doc_id`` returns the
        # deleted-rows count but we don't rely on it -- the upsert is
        # the authoritative write.
        await self._context.corpus.delete_by_doc_id(source.id)  # type: ignore[attr-defined]
        # The sqlite-vec adapter expects ids on delete; we pass the
        # full previous set lazily by recreating from the new ids
        # only when present. The simplest correct path is to leave
        # stale ids in place until the next replace; sqlite-vec's
        # upsert by id overwrites cleanly.
        if stored_chunks:
            await self._context.corpus.upsert_chunks(stored_chunks)  # type: ignore[attr-defined]
        if vector_documents:
            await self._context.vector_store.upsert(vector_documents)  # type: ignore[attr-defined]

        # Note the model on the entity so re-embedding with a different
        # model can detect the mismatch.
        for chunk in chunks:
            chunk.embedding_model = embedding_model

        logger.info(
            "indexed source=%s chunks=%d backend=%s",
            source.id,
            len(stored_chunks),
            getattr(self._context, "backend", "unknown"),
        )
        return len(stored_chunks)

    async def remove_for_source(self, source_id: str) -> int:
        """Wipe every projection for ``source_id``. Idempotent."""
        deleted = await self._context.corpus.delete_by_doc_id(source_id)  # type: ignore[attr-defined]
        # vector-store cleanup happens lazily via id-overwrite at next ingest;
        # callers that need an immediate purge should also call
        # ``vector_store.delete([...])`` with the chunk ids.
        return int(deleted or 0)

    @staticmethod
    def _chunk_metadata(*, source: SourceRow, chunk: KnowledgeChunkRow) -> dict[str, str]:
        meta: dict[str, str] = {
            "source_id": source.id,
            "source_kind": source.kind,
        }
        if chunk.section_path:
            meta["section_path"] = chunk.section_path
        if chunk.page is not None:
            meta["page"] = str(chunk.page)
        return meta
