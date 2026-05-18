# Copyright 2026 Firefly Software Solutions Inc
"""Typed errors for the ingestion pipeline."""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for every ingestion failure.

    Subclasses carry a stable ``code`` attribute the exception advice
    maps to ``ProblemDetails.code`` so SDKs can branch on the failure
    mode without parsing ``detail``.
    """

    code: str = "ingestion_failed"
    http_status: int = 422


class UnsupportedSourceKind(IngestionError):
    """Raised when the requested ``SourceKind`` has no registered loader."""

    code = "unsupported_source_kind"

    def __init__(self, kind: str) -> None:
        super().__init__(f"no loader is registered for source kind {kind!r}")
        self.kind = kind


class EmptySource(IngestionError):
    """Raised when the loader produces no text content."""

    code = "empty_source"

    def __init__(self) -> None:
        super().__init__("the source produced no extractable text content")


class CorruptSource(IngestionError):
    """Raised when the loader fails to parse the bytes."""

    code = "corrupt_source"

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"could not parse {kind!r} source: {detail}")
        self.kind = kind
