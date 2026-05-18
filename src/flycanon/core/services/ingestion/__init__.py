# Copyright 2026 Firefly Software Solutions Inc
"""Source intake -- loaders, chunking, and the ingestion service.

The intake pipeline turns inbound bytes into a sequence of
:class:`Chunk` rows ready for embedding + indexing. The pipeline is
loader-agnostic: each :class:`SourceLoader` produces a normalised
:class:`LoadedDocument`, and the chunker shards the document into
retrieval-grade fragments with heading-path metadata.

Format coverage:

* **DOCX**       -- :class:`DocxLoader` (Heading1-9 detection).
* **PDF**        -- :class:`PdfLoader` (pypdf, born-digital).
* **HTML**       -- :class:`HtmlLoader` (BeautifulSoup, h1-h6).
* **Markdown**   -- :class:`MarkdownLoader` (literal headings).
* **Text**       -- :class:`TextLoader` (single section).
* **Image**      -- :class:`ImageLoader` (Tesseract OCR).
* **Transcript** -- :class:`TranscriptLoader` (WebVTT / SRT).
* **XLSX / PPTX / RTF / ODT / ODS / ODP / CSV / EPUB /
  JSON / XML**   -- :class:`MarkitdownLoader` (universal fallback).
"""

from __future__ import annotations

from flycanon.core.services.ingestion.chunker import Chunk, Chunker, ParagraphChunker
from flycanon.core.services.ingestion.errors import (
    CorruptSource,
    EmptySource,
    IngestionError,
    UnsupportedSourceKind,
)
from flycanon.core.services.ingestion.ingestion_service import IngestionResult, IngestionService
from flycanon.core.services.ingestion.loaders import (
    DocxLoader,
    HtmlLoader,
    ImageLoader,
    LoadedDocument,
    LoaderRegistry,
    MarkdownLoader,
    MarkitdownLoader,
    PdfLoader,
    Section,
    SourceLoader,
    TextLoader,
    TranscriptLoader,
)

__all__ = [
    "Chunk",
    "Chunker",
    "CorruptSource",
    "DocxLoader",
    "EmptySource",
    "HtmlLoader",
    "ImageLoader",
    "IngestionError",
    "IngestionResult",
    "IngestionService",
    "LoadedDocument",
    "LoaderRegistry",
    "MarkdownLoader",
    "MarkitdownLoader",
    "ParagraphChunker",
    "PdfLoader",
    "Section",
    "SourceLoader",
    "TextLoader",
    "TranscriptLoader",
    "UnsupportedSourceKind",
]
