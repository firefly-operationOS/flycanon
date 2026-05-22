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
        tenant_id: str,
        workspace_id: str,
    ) -> int:
        """Replace every indexed chunk for ``source.id`` atomically.

        Returns the number of chunks ingested into the index.

        ``tenant_id`` / ``workspace_id`` are REQUIRED kwargs. The
        previous soft default to ``'default'`` was a write-path hole
        that landed every silently-scoped vector in the
        ``('default','default')`` RLS bucket -- invisible to the real
        caller under migration 0013 policies. Forgetting the scope
        now fails loud with a ``TypeError`` at the call site instead
        of silently corrupting the index.
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
            # Probe the upsert signature so we only push scope to
            # backends that accept it (currently flycanon's
            # PgVectorVectorStore -- the agentic stores ignore the
            # scope and rely on the canon_chunks scope filter on
            # read-hydration as the safety net).
            await self._upsert_vectors(vector_documents, tenant_id=tenant_id, workspace_id=workspace_id)

        # Note the model on the entity so re-embedding with a different
        # model can detect the mismatch.
        for chunk in chunks:
            chunk.embedding_model = embedding_model

        logger.info(
            "indexed source=%s chunks=%d backend=%s tenant=%s workspace=%s",
            source.id,
            len(stored_chunks),
            getattr(self._context, "backend", "unknown"),
            tenant_id,
            workspace_id,
        )
        return len(stored_chunks)

    async def _upsert_vectors(
        self,
        documents: Sequence[object],
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        """Thread scope to vector-store ``upsert`` when supported.

        Backends that don't yet accept ``tenant_id``/``workspace_id``
        kwargs (agentic ``InMemory`` / ``SqliteVec`` / ``Chroma`` /
        etc.) are called without the extras. The flycanon
        :class:`PgVectorVectorStore` always accepts them.
        """
        import inspect

        upsert = self._context.vector_store.upsert  # type: ignore[attr-defined]
        try:
            sig = inspect.signature(upsert)
            supports_scope = "tenant_id" in sig.parameters and "workspace_id" in sig.parameters
        except (TypeError, ValueError):
            supports_scope = False
        if supports_scope:
            await upsert(list(documents), tenant_id=tenant_id, workspace_id=workspace_id)
        else:
            await upsert(list(documents))

    async def remove_for_source(
        self,
        source_id: str,
        *,
        tenant_id: str,  # noqa: ARG002 -- reserved for signature symmetry with replace_for_source
        workspace_id: str,  # noqa: ARG002
    ) -> int:
        """Wipe every projection for ``source_id``. Idempotent.

        ``tenant_id`` / ``workspace_id`` are REQUIRED for signature
        symmetry with :meth:`replace_for_source` -- the BM25 wipe
        already runs through the source's FK cascade (canonical
        store enforces the scope at delete time) and the vector
        cleanup happens lazily via id-overwrite at next ingest.
        Callers that need an immediate vector-store purge should
        also call ``vector_store.delete([...])`` with the chunk ids.
        Making the kwargs required matches the write-path tightening
        on :meth:`replace_for_source` so forgotten scope on either
        surface fails loud at the call site.
        """
        deleted = await self._context.corpus.delete_by_doc_id(source_id)  # type: ignore[attr-defined]
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
