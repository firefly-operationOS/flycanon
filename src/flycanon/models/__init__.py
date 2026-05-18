# Copyright 2026 Firefly Software Solutions Inc
"""flycanon persistence layer.

Owns the ORM entities (``flycanon.models.entities``) and the thin
async repositories that wrap them (``flycanon.models.repositories``).
Nothing in this package should depend on ``flycanon.core`` or
``flycanon.web``; the dependency arrow points the other way.
"""

from __future__ import annotations

from flycanon.models.repositories import (
    AuditRepository,
    CandidateRepository,
    ChunkRepository,
    KnowledgeRepository,
    SourceRepository,
    TaxonomyRepository,
)

__all__ = [
    "AuditRepository",
    "CandidateRepository",
    "ChunkRepository",
    "KnowledgeRepository",
    "SourceRepository",
    "TaxonomyRepository",
]
