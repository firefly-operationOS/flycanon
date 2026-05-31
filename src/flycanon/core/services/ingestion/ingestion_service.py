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

"""High-level ingestion orchestrator.

The :class:`IngestionService` runs the loader + chunker against one
inbound binary (already normalised by :class:`BinaryNormalizer`). The
intake pipeline above this layer takes care of:

* multi-format binary detection (sniffer),
* image format normalisation (HEIC / AVIF / TIFF / BMP / SVG -> PNG),
* archive fan-out (ZIP / 7Z / TAR / GZ),
* email decomposition (EML / MSG -> body + attachments),
* optional Office -> PDF conversion (Gotenberg / LibreOffice).

By the time bytes reach this service the kind is canonical and the
loader registry can dispatch directly to the right loader (or fall
through to the plain-text :class:`TextLoader` for kinds not explicitly
registered).
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

    ``chunks`` is fresh on every call -- callers persist them through
    :class:`ChunkRepository`. The ``source`` row is mutated in place
    so callers can refresh status / counters in their own session.
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
        tenant_id: str,
        workspace_id: str,
    ) -> IngestionResult:
        bytes_content = content.encode("utf-8") if isinstance(content, str) else content

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
                tenant_id=tenant_id,
                workspace_id=workspace_id,
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

        Used by callers that don't want to wire the magic-byte sniffer
        (e.g. JSON-only ingestion paths). The :class:`BinaryNormalizer`
        re-runs sniffing internally; this stays as a fast hint for
        the controllers and the SDKs.
        """
        ct = (content_type or "").lower()
        name = (filename or "").lower()

        if "wordprocessingml" in ct or name.endswith(".docx"):
            return SourceKind.docx
        if "spreadsheetml" in ct or name.endswith(".xlsx"):
            return SourceKind.xlsx
        if "presentationml" in ct or name.endswith(".pptx"):
            return SourceKind.pptx
        if "opendocument.text" in ct or name.endswith(".odt"):
            return SourceKind.odt
        if "opendocument.spreadsheet" in ct or name.endswith(".ods"):
            return SourceKind.ods
        if "opendocument.presentation" in ct or name.endswith(".odp"):
            return SourceKind.odp
        if "application/rtf" in ct or name.endswith(".rtf"):
            return SourceKind.rtf
        if "application/pdf" in ct or name.endswith(".pdf"):
            return SourceKind.pdf
        if "text/html" in ct or name.endswith((".html", ".htm")):
            return SourceKind.html
        if "text/markdown" in ct or name.endswith((".md", ".markdown")):
            return SourceKind.markdown
        if "text/csv" in ct or name.endswith(".csv"):
            return SourceKind.csv
        if "tab-separated" in ct or name.endswith(".tsv"):
            return SourceKind.tsv
        if "application/json" in ct or name.endswith(".json"):
            return SourceKind.json_
        if "application/xml" in ct or name.endswith(".xml"):
            return SourceKind.xml
        if "epub" in ct or name.endswith(".epub"):
            return SourceKind.epub
        if "text/plain" in ct or name.endswith((".txt", ".text")):
            return SourceKind.text
        _image_exts = (
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".heic",
            ".heif",
            ".avif",
            ".tif",
            ".tiff",
            ".bmp",
            ".svg",
        )
        if ct.startswith("image/") or name.endswith(_image_exts):
            return SourceKind.image
        if "zip" in ct or name.endswith((".zip", ".7z", ".tar", ".gz", ".tgz")):
            return SourceKind.archive
        if "message/rfc822" in ct or name.endswith((".eml", ".msg")) or "ms-outlook" in ct:
            return SourceKind.email
        if name.endswith((".vtt", ".srt")):
            return SourceKind.transcript
        return SourceKind.unknown
