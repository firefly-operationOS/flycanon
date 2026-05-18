# Copyright 2026 Firefly Software Solutions Inc
"""``SourceRow`` -> :class:`SourceRecord` DTO."""

from __future__ import annotations

from flycanon.interfaces.dtos.source import SourceMetadata, SourceRecord
from flycanon.interfaces.enums import Domain, Jurisdiction, SourceKind, SourceStatus
from flycanon.models.entities.source import SourceRow


def _coerce_metadata(raw: dict | None) -> SourceMetadata:
    data = dict(raw or {})
    domain = data.get("domain")
    jurisdiction = data.get("jurisdiction")
    return SourceMetadata(
        title=data.get("title"),
        author=data.get("author"),
        domain=Domain(domain) if domain in Domain._value2member_map_ else None,
        jurisdiction=(
            Jurisdiction(jurisdiction) if jurisdiction in Jurisdiction._value2member_map_ else None
        ),
        language=data.get("language"),
        tags=list(data.get("tags") or []),
        extra={
            k: v
            for k, v in data.items()
            if k not in {"title", "author", "domain", "jurisdiction", "language", "tags"}
        },
    )


def to_source_record(row: SourceRow) -> SourceRecord:
    return SourceRecord(
        id=row.id,
        kind=SourceKind(row.kind),
        status=SourceStatus(row.status),
        filename=row.filename,
        uri=row.uri,
        content_sha256=row.content_sha256,
        content_bytes=row.content_bytes,
        n_chunks=row.n_chunks,
        metadata=_coerce_metadata(row.metadata_json),
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        ingested_at=row.ingested_at,
        updated_at=row.updated_at,
    )
