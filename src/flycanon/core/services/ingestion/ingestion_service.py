# Copyright 2026 Firefly Software Solutions Inc
"""High-level ingestion orchestrator.

The :class:`IngestionService` is the single entry point the rest of
flycanon uses to turn caller-supplied bytes into a populated
:class:`SourceRow` plus a list of :class:`KnowledgeChunkRow`. It owns
neither embedding nor indexing -- those are downstream stages wired in
the CQRS handler. Keeping the responsibilities separate lets the
ingestion path be reused by:

* the sync REST endpoint (``POST /api/v1/sources``),
* the async EDA worker (``flycanon worker``),
* test fixtures that need a fully-populated source row without going
  through HTTP.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from flycanon.core.services.ingestion.chunker import Chunker
from flycanon.core.services.ingestion.errors import EmptySource, UnsupportedSourceKind
from flycanon.core.services.ingestion.loaders import LoaderRegistry
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.source import SourceRow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestionResult:
    """The output of one ingestion call.

    ``chunks`` is fresh on every call -- callers are responsible for
    persisting them through :class:`ChunkRepository`. The ``source``
    row is mutated in place so callers can refresh status / counters
    in their own session.
    """

    source: SourceRow
    chunks: list[KnowledgeChunkRow]


class IngestionService:
    """Bytes + caller hints -> populated :class:`SourceRow` + chunks."""

    def __init__(self, *, loaders: LoaderRegistry, chunker: Chunker) -> None:
        self._loaders = loaders
        self._chunker = chunker

    def ingest(
        self,
        *,
        source: SourceRow,
        content: bytes | str,
    ) -> IngestionResult:
        """Run the loader + chunker against ``content`` and update ``source``.

        Pre-conditions:

        * ``source.kind`` is set (use :meth:`detect_kind` first if the
          caller did not specify one).
        * ``source.id`` is set (the caller is responsible for assigning
          an id before invoking the service -- this keeps the
          chunk-to-source FK trivial).
        """
        bytes_content = content.encode("utf-8") if isinstance(content, str) else content

        # Idempotency anchor: SHA-256 of the canonical bytes.
        source.content_sha256 = hashlib.sha256(bytes_content).hexdigest()
        source.content_bytes = len(bytes_content)
        source.status = SourceStatus.ingesting.value

        kind = SourceKind(source.kind) if isinstance(source.kind, str) else source.kind
        loader = self._loaders.get(kind)
        if loader is None:
            raise UnsupportedSourceKind(kind.value)

        document = loader.load(bytes_content, filename=source.filename)
        if not document.sections:
            raise EmptySource()

        chunk_dtos = self._chunker.chunk(document)
        if not chunk_dtos:
            raise EmptySource()

        chunks: list[KnowledgeChunkRow] = [
            KnowledgeChunkRow(
                source_id=source.id,
                index_in_source=chunk.index_in_source,
                total_chunks=chunk.total_chunks,
                content=chunk.content,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                page=chunk.page,
                section_path=chunk.section_path,
                metadata_json=dict(chunk.metadata),
            )
            for chunk in chunk_dtos
        ]

        source.n_chunks = len(chunks)
        source.status = SourceStatus.ingested.value
        source.ingested_at = datetime.now(UTC)
        # Propagate document-level metadata when present.
        meta = dict(source.metadata_json or {})
        if document.title and "title" not in meta:
            meta["title"] = document.title
        if document.language and "language" not in meta:
            meta["language"] = document.language
        if document.page_count is not None:
            meta["page_count"] = document.page_count
        source.metadata_json = meta

        logger.info(
            "ingested source id=%s kind=%s n_chunks=%d sha=%s",
            source.id,
            source.kind,
            source.n_chunks,
            source.content_sha256[:8],
        )
        return IngestionResult(source=source, chunks=chunks)

    @staticmethod
    def detect_kind(*, filename: str | None, content_type: str | None) -> SourceKind:
        """Pick a :class:`SourceKind` from filename + content-type hints.

        Falls back to ``SourceKind.unknown``; the caller may either
        retry with an explicit kind or treat the source as
        ``unsupported_source_kind`` upstream.
        """
        ct = (content_type or "").lower()
        if "wordprocessingml.document" in ct or "officedocument" in ct or "docx" in ct:
            return SourceKind.docx
        if "pdf" in ct:
            return SourceKind.pdf
        if "html" in ct:
            return SourceKind.html
        if "markdown" in ct:
            return SourceKind.markdown
        if "text/plain" in ct:
            return SourceKind.text

        name = (filename or "").lower()
        if name.endswith(".docx"):
            return SourceKind.docx
        if name.endswith(".pdf"):
            return SourceKind.pdf
        if name.endswith((".html", ".htm")):
            return SourceKind.html
        if name.endswith((".md", ".markdown")):
            return SourceKind.markdown
        if name.endswith((".txt", ".text")):
            return SourceKind.text
        return SourceKind.unknown
