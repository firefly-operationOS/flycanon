# Copyright 2026 Firefly Software Solutions Inc
"""``SourceKind`` -- the canonical wire identifier for a source format.

Each value maps to one loader in the ingestion pipeline. ``unknown`` is
reserved for sources whose kind is undetermined at submission time;
the ingestion pipeline either detects it from the content-type / magic
bytes or fails with ``unsupported_source_kind``.
"""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    docx = "docx"
    pdf = "pdf"
    html = "html"
    markdown = "markdown"
    text = "text"
    transcript = "transcript"
    url = "url"
    unknown = "unknown"
