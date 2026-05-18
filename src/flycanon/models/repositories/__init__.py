# Copyright 2026 Firefly Software Solutions Inc
"""Async repositories for every flycanon entity.

Each repository owns one table. They share the same constructor /
``from_url`` factory pattern so the DI container can instantiate them
uniformly and the actuator's database health probe can reach the
underlying async engine through the ``engine`` property.
"""

from __future__ import annotations

from flycanon.models.repositories.audit_repository import AuditRepository
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.conversation_repository import ConversationRepository
from flycanon.models.repositories.cost_repository import CostRepository
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.relation_repository import RelationRepository
from flycanon.models.repositories.source_repository import SourceRepository
from flycanon.models.repositories.taxonomy_repository import TaxonomyRepository

__all__ = [
    "AuditRepository",
    "CandidateRepository",
    "ChunkRepository",
    "ConversationRepository",
    "CostRepository",
    "IngestJobRepository",
    "KnowledgeRepository",
    "RelationRepository",
    "SourceRepository",
    "TaxonomyRepository",
]
