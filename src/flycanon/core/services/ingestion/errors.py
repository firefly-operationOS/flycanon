# Copyright 2026 Firefly Software Solutions Inc
"""Typed errors for the ingestion pipeline.

Each subclass inherits from :class:`FireflyHTTPException` so the
conventions exception handler renders it as an RFC 7807
``ProblemDetail`` automatically. The legacy
``flycanon.web.problem_handlers`` still catches each subclass by
class during the Phase 4 migration window.

``http_status`` is preserved alongside the new ClassVar ``status``
so the legacy parent handler -- which reads
``getattr(exc, "http_status", 422)`` -- continues to work during the
window. Both attributes are kept in sync.
"""

from __future__ import annotations

from typing import ClassVar

from flycanon.web.conventions import FireflyHTTPException


class IngestionError(FireflyHTTPException):
    """Base class for every ingestion failure.

    Subclasses carry a stable ``code`` attribute the conventions
    handler maps to ``ProblemDetail.code`` so SDKs can branch on the
    failure mode without parsing ``detail``.
    """

    status: ClassVar[int] = 422
    code: ClassVar[str] = "ingestion_failed"
    title: ClassVar[str] = "Source ingestion failed"
    http_status: ClassVar[int] = 422


class UnsupportedSourceKind(IngestionError):
    """Raised when the requested ``SourceKind`` has no registered loader."""

    status = 415
    code = "unsupported_source_kind"
    title = "Unsupported source kind"
    http_status = 415

    def __init__(self, kind: str) -> None:
        super().__init__(f"no loader is registered for source kind {kind!r}")
        self.kind = kind


class EmptySource(IngestionError):
    """Raised when the loader produces no text content."""

    status = 422
    code = "empty_source"
    title = "Empty source"
    http_status = 422

    def __init__(self) -> None:
        super().__init__("the source produced no extractable text content")


class CorruptSource(IngestionError):
    """Raised when the loader fails to parse the bytes."""

    status = 422
    code = "corrupt_source"
    title = "Corrupt source"
    http_status = 422

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"could not parse {kind!r} source: {detail}")
        self.kind = kind
