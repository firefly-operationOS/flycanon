# Copyright 2026 Firefly Software Solutions Inc
"""Typed exceptions for the source-intake surface."""

from __future__ import annotations


class SourceNotFound(Exception):
    """Raised when a source id is unknown to the canonical store.

    Maps to RFC 7807 ``404 source_not_found`` through the controller
    advice. Used by the replace + (future) delete flows.
    """

    code = "source_not_found"
    http_status = 404

    def __init__(self, source_id: str) -> None:
        super().__init__(f"source {source_id!r} not found")
        self.source_id = source_id
