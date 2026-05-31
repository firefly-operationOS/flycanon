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

"""Source intake -- loaders, chunking, and the ingestion service.

The intake pipeline turns inbound bytes into a sequence of
:class:`Chunk` rows ready for embedding + indexing. The pipeline is
loader-agnostic: each :class:`SourceLoader` produces a normalised
:class:`LoadedDocument`, and the chunker shards the document into
retrieval-grade fragments with heading-path metadata.

Format coverage:

* **DOCX**       -- :class:`DocxLoader` (Heading1-9 detection).
* **PDF**        -- :class:`PdfLoader` (PyMuPDF + Tesseract OCR).
* **HTML**       -- :class:`HtmlLoader` (BeautifulSoup, h1-h6).
* **Markdown**   -- :class:`MarkdownLoader` (literal headings).
* **Text**       -- :class:`TextLoader` (single section).
* **Image**      -- :class:`ImageLoader` (Tesseract OCR).
* **Transcript** -- :class:`TranscriptLoader` (WebVTT / SRT).
* **XLSX**       -- :class:`XlsxLoader` (openpyxl, one section per sheet).
* **PPTX**       -- :class:`PptxLoader` (python-pptx, one section per slide).
* **CSV / TSV**  -- :class:`CsvLoader` (stdlib ``csv``).
* **JSON**       -- :class:`JsonLoader` (stdlib ``json``).
* **XML**        -- :class:`XmlLoader` (lxml, recovering parser).
* **RTF**        -- :class:`RtfLoader` (striprtf).
* **ODT/ODS/ODP** -- :class:`OdfLoader` (odfpy).

Unrecognised payloads fall back to :class:`TextLoader`.
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
    CsvLoader,
    DocxLoader,
    HtmlLoader,
    ImageLoader,
    JsonLoader,
    LoadedDocument,
    LoaderRegistry,
    MarkdownLoader,
    OdfLoader,
    PdfLoader,
    PptxLoader,
    RtfLoader,
    Section,
    SourceLoader,
    TextLoader,
    TranscriptLoader,
    XlsxLoader,
    XmlLoader,
)

__all__ = [
    "Chunk",
    "Chunker",
    "CorruptSource",
    "CsvLoader",
    "DocxLoader",
    "EmptySource",
    "HtmlLoader",
    "ImageLoader",
    "IngestionError",
    "IngestionResult",
    "IngestionService",
    "JsonLoader",
    "LoadedDocument",
    "LoaderRegistry",
    "MarkdownLoader",
    "OdfLoader",
    "ParagraphChunker",
    "PdfLoader",
    "PptxLoader",
    "RtfLoader",
    "Section",
    "SourceLoader",
    "TextLoader",
    "TranscriptLoader",
    "UnsupportedSourceKind",
    "XlsxLoader",
    "XmlLoader",
]
