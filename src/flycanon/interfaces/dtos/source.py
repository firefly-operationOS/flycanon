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

"""Source-intake DTOs.

A *source* is the raw inbound artefact -- a DOCX, a PDF, an HTML
page, a ZIP archive, an email, an image to OCR, ... -- that flycanon
ingests into the canonical knowledge fabric. The intake pipeline
treats every format uniformly:

1. Magic-byte sniff to detect the real media type.
2. Route through the binary normaliser (image conversion, archive
   expansion, email decomposition, optional Office -> PDF).
3. Load via the per-format loader (DocxLoader, PdfLoader,
   HtmlLoader, MarkdownLoader, ImageLoader (OCR), TranscriptLoader,
   or the plain-text TextLoader fallback).
4. Chunk into retrieval-grade fragments with heading-path metadata.
5. Embed every chunk via the configured provider.
6. Index BM25 (Postgres tsvector + GIN on ``canon_chunks``) + dense
   vectors (pgvector), both on the canonical Postgres.
7. Audit + broadcast ``SourceIngested`` on ``flycanon.ingest``.

The canonical bytes are never stored. The :class:`SourceRecord`
carries the SHA-256 content hash (idempotency anchor), the chunk
count, and the metadata; chunks live in ``canon_chunks`` and the
retrieval index.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flycanon.interfaces.enums import Domain, Jurisdiction, SourceKind, SourceStatus


class SourceMetadata(BaseModel):
    """Caller-supplied metadata that travels with a source.

    The intake pipeline only fills in fields the caller did not
    specify -- ``content_type`` from the upload's MIME, ``title``
    from the document properties when present, ``language`` from
    the loader's detection. The :class:`SourceMetadata.extra` dict
    is a free-form passthrough preserved on the source row's
    ``metadata_json`` column so downstream projections can use any
    custom field without a schema change.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(
        default=None,
        description=(
            "Human-readable title for the source. Surfaces on the "
            "/api/v1/sources list view, the provenance graph, and "
            "every audit row that references this source."
        ),
        examples=["CANON Operational Knowledge -- MVP scope"],
    )
    author: str | None = Field(
        default=None,
        description="Document author or originator. Free text.",
        examples=["Elena Boffa Tarlatta"],
    )
    domain: Domain | None = Field(
        default=None,
        description=(
            "Operational domain the source belongs to. Mirrors the "
            "workshop persona map -- one of legal / compliance / "
            "process / network / ai_platform / executive / hr / cto "
            "/ engineering / security / other. When omitted, the "
            "candidate consolidation stage picks the best fit."
        ),
        examples=["process"],
    )
    jurisdiction: Jurisdiction | None = Field(
        default=None,
        description=(
            "Geographic / legal scope of the source. ISO 3166-1 "
            "alpha-2 country codes plus EU / EMEA / LATAM / APAC / "
            "AMER / GLOBAL shorthands."
        ),
        examples=["ES", "EU", "GLOBAL"],
    )
    language: str | None = Field(
        default=None,
        description=(
            "ISO 639-1 language hint (``es``, ``en``, ``pt``, ...). "
            "Auto-detected from the loader when omitted; used as the "
            "default OCR ``-l`` argument for raster sources."
        ),
        examples=["es", "en"],
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Free-form tags for downstream filtering. Applied as a "
            "metadata column on every chunk so retrieval can scope "
            "by tag without a join."
        ),
        examples=[["mvp", "workshop"]],
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Caller-defined fields preserved on the source row's "
            "metadata_json. The intake pipeline does not interpret "
            "them; they ride through to the audit trail and the "
            "provenance graph."
        ),
        examples=[{"team": "operations-transformation", "release": "26.05"}],
    )


class SubmitSourceRequest(BaseModel):
    """JSON-only submission payload.

    The companion ``content_base64`` field on the controller-side
    :class:`SubmitSourceJsonPayload` carries the canonical bytes
    encoded as base64; this base model exposes the metadata that
    travels with every kind of submission so the SDK can build it
    independently of the wire encoding.
    """

    kind: SourceKind = Field(
        default=SourceKind.unknown,
        description=(
            "Caller hint for the source format. When ``unknown`` "
            "(the default) the binary normaliser sniffs the actual "
            "type from magic bytes + the filename extension; the "
            "hint is a fast-path for callers that already know."
        ),
        examples=["docx", "pdf", "xlsx", "html", "image", "archive", "email"],
    )
    uri: str | None = Field(
        default=None,
        description=(
            "Origin URI when the caller is ingesting a fetch-by-URL "
            "source. Currently treated as a tracking field only -- "
            "URL fetching is on the roadmap; v1 callers must send "
            "the bytes via ``content_base64``."
        ),
        examples=["https://example.com/policies/2026-procedure.pdf"],
    )
    metadata: SourceMetadata = Field(
        default_factory=SourceMetadata,
        description="Caller-supplied metadata propagated to the source row.",
    )


class SourceRecord(BaseModel):
    """Public view of a ``canon_sources`` row.

    Returned by ``POST /api/v1/sources`` (201) and ``GET /api/v1/sources/{id}``.
    Carries enough state for downstream consumers to subscribe to
    lifecycle events and reconstruct the intake path without a
    second round-trip.
    """

    id: str = Field(
        description="Stable string UUID assigned at intake.",
        examples=["0b06a8e6-37e7-4f7b-a8f1-7e2a6f7e5d11"],
    )
    kind: SourceKind = Field(
        description="Canonical source format detected by the binary normaliser.",
        examples=["docx"],
    )
    status: SourceStatus = Field(
        description=(
            "Lifecycle state. Terminal: ``ingested`` on success, "
            "``failed`` when normalisation / loading rejected the "
            "bytes (``error_code`` carries the reason), "
            "``superseded`` when a later upload of the same content "
            "hash replaces this row."
        ),
        examples=["ingested"],
    )
    filename: str | None = Field(
        default=None,
        description="Original filename when uploaded; None for URL-fetched sources.",
        examples=["CANON-Operational-Knowledge.docx"],
    )
    uri: str | None = Field(
        default=None,
        description="Origin URI when the caller referenced one.",
    )
    content_sha256: str = Field(
        description=(
            "SHA-256 of the canonical bytes (after binary "
            "normalisation). Used as the idempotency anchor -- two "
            "uploads of the same bytes share a SourceRecord."
        ),
        examples=["8b3c0d1a9f4b..."],
    )
    content_bytes: int = Field(
        ge=0,
        description="Size of the canonical bytes after normalisation.",
        examples=[915450],
    )
    n_chunks: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of retrieval-grade chunks emitted by the chunker. "
            "Each chunk lands in ``canon_chunks`` and is indexed for "
            "BM25 + vector retrieval."
        ),
        examples=[84],
    )
    metadata: SourceMetadata = Field(
        default_factory=SourceMetadata,
        description=(
            "Caller-supplied metadata + loader-extracted fields "
            "(``title``, ``language``, ``page_count``, plus the "
            "binary normaliser's artifact ancestry when applicable)."
        ),
    )
    error_code: str | None = Field(
        default=None,
        description=(
            "Stable machine-readable failure code (e.g. "
            "``unsupported_binary``, ``encrypted_pdf``, ``empty_source``) "
            "set when ``status == failed``."
        ),
    )
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(description="When the row was first persisted.")
    ingested_at: datetime | None = Field(
        default=None,
        description="When ``status`` flipped to ``ingested`` (None on failure).",
    )
    updated_at: datetime = Field(description="Last mutation timestamp; auto-bumped on status changes.")


class SourcesPage(BaseModel):
    """Paged list of source records.

    Returned by ``GET /api/v1/sources``. The total reflects the
    filtered set, not the page; callers paginate via
    ``offset`` / ``limit`` against ``total``.
    """

    items: list[SourceRecord] = Field(description="Page of source records, newest first.")
    total: int = Field(ge=0, description="Total rows matching the filters.")
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)


class BulkSourceResult(BaseModel):
    """Per-entry outcome of a bulk ingest call.

    Either ``source`` is populated (on success) or
    ``error_code`` + ``error_message`` are (on failure). The
    ``index`` field matches the input array position so callers
    can correlate failures with their batch.
    """

    index: int = Field(ge=0, description="Position in the request array.")
    status: str = Field(
        description="``succeeded`` or ``failed`` for this entry.",
        examples=["succeeded", "failed"],
    )
    source: SourceRecord | None = Field(default=None)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)


class BulkSourcesResponse(BaseModel):
    """Aggregate response for ``POST /api/v1/sources:bulk``.

    Entries are processed sequentially -- failures are isolated to
    their own entry and never abort the batch. The caller inspects
    each ``BulkSourceResult`` to retry / inspect failures.
    """

    results: list[BulkSourceResult] = Field(default_factory=list)
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
