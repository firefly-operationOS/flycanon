# Copyright 2026 Firefly Software Solutions Inc
"""Source intake -- loaders, chunking, and the ingestion service.

The intake pipeline turns inbound bytes into a sequence of
:class:`Chunk` rows ready for embedding + indexing. The pipeline is
loader-agnostic: each :class:`SourceLoader` produces a normalised
:class:`LoadedDocument`, and the chunker shards the document into
retrieval-grade fragments with heading-path metadata.
"""

from __future__ import annotations

from flycanon.core.services.ingestion.chunker import Chunk, Chunker, ParagraphChunker
from flycanon.core.services.ingestion.errors import IngestionError, UnsupportedSourceKind
from flycanon.core.services.ingestion.ingestion_service import IngestionResult, IngestionService
from flycanon.core.services.ingestion.loaders import (
    DocxLoader,
    HtmlLoader,
    LoadedDocument,
    LoaderRegistry,
    MarkdownLoader,
    PdfLoader,
    Section,
    SourceLoader,
    TextLoader,
)

__all__ = [
    "Chunk",
    "Chunker",
    "DocxLoader",
    "HtmlLoader",
    "IngestionError",
    "IngestionResult",
    "IngestionService",
    "LoadedDocument",
    "LoaderRegistry",
    "MarkdownLoader",
    "ParagraphChunker",
    "PdfLoader",
    "Section",
    "SourceLoader",
    "TextLoader",
    "UnsupportedSourceKind",
]
