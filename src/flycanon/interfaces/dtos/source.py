# Copyright 2026 Firefly Software Solutions Inc
"""Source-intake DTOs.

A source is the raw inbound artefact (a DOCX, a PDF, an HTML page,
...). Ingesting one persists the bytes' metadata, the chunked content,
the embeddings for every chunk, and the BM25 projection. The
canonical knowledge layer only sees sources through citations -- the
bytes never leave the corpus boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flycanon.interfaces.enums import Domain, Jurisdiction, SourceKind, SourceStatus


class SourceMetadata(BaseModel):
    """Caller-supplied metadata that travels with a source.

    Every field here is optional; the ingestion pipeline fills in
    ``content_type`` from the upload's MIME if not provided and leaves
    the rest untouched.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, description="Human-readable title.")
    author: str | None = Field(default=None, description="Document author or originator.")
    domain: Domain | None = Field(default=None, description="Operational domain the source belongs to.")
    jurisdiction: Jurisdiction | None = Field(
        default=None,
        description="Geographic / legal scope of the source.",
    )
    language: str | None = Field(
        default=None,
        description="ISO 639-1 language hint (``es``, ``en``, ...). Auto-detected if omitted.",
        examples=["es", "en"],
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Free-form tags for downstream filtering.",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Caller-defined fields propagated to the audit trail and SourceRecord.",
    )


class SubmitSourceRequest(BaseModel):
    """JSON-only submission payload (used by the URL ingest path).

    File uploads use multipart and carry the bytes alongside this
    model serialised as ``application/json``; both paths feed the
    same handler.
    """

    kind: SourceKind = Field(default=SourceKind.unknown, description="Canonical source format.")
    uri: str | None = Field(
        default=None,
        description="Origin URI when the caller is referencing a fetch-by-URL source.",
        examples=["https://example.com/policy.html"],
    )
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)


class SourceRecord(BaseModel):
    """Public view of a source row."""

    id: str
    kind: SourceKind
    status: SourceStatus
    filename: str | None = Field(default=None, description="Original filename, if uploaded.")
    uri: str | None = Field(default=None)
    content_sha256: str = Field(description="SHA-256 of the canonical bytes (idempotency key).")
    content_bytes: int = Field(ge=0, description="Size in bytes after normalisation.")
    n_chunks: int = Field(default=0, ge=0, description="Number of indexed chunks.")
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    created_at: datetime
    ingested_at: datetime | None = Field(default=None)
    updated_at: datetime


class SourcesPage(BaseModel):
    """Paged list of source records."""

    items: list[SourceRecord]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
