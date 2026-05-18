# Copyright 2026 Firefly Software Solutions Inc
"""``SourceStatus`` -- lifecycle of an ingested source.

State transitions are append-only and recorded in ``audit_events``.
Terminal states are ``ingested`` (success), ``failed`` (loader or
indexer rejected the bytes), and ``superseded`` (the same content hash
was re-uploaded under a different source id).
"""

from __future__ import annotations

from enum import StrEnum


class SourceStatus(StrEnum):
    pending = "pending"
    ingesting = "ingesting"
    ingested = "ingested"
    failed = "failed"
    superseded = "superseded"
