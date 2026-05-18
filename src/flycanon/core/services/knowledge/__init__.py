# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge-item lifecycle service.

Owns the state machine for canonical knowledge -- create, update,
supersede, retire -- and emits the corresponding events on the
``flycanon.knowledge`` topic. Every transition is mirrored in the
audit log through :class:`AuditService`.
"""

from __future__ import annotations

from flycanon.core.services.knowledge.diff_service import KnowledgeDiffService
from flycanon.core.services.knowledge.errors import (
    KnowledgeItemAlreadyRetired,
    KnowledgeItemNotFound,
    KnowledgeServiceError,
)
from flycanon.core.services.knowledge.graph_service import KnowledgeGraphService
from flycanon.core.services.knowledge.knowledge_service import KnowledgeService
from flycanon.core.services.knowledge.provenance_service import ProvenanceService
from flycanon.core.services.knowledge.relation_service import (
    InvalidRelationError,
    KnowledgeRelationService,
    RelationConflictError,
    RelationNotFoundError,
)

__all__ = [
    "InvalidRelationError",
    "KnowledgeDiffService",
    "KnowledgeGraphService",
    "KnowledgeItemAlreadyRetired",
    "KnowledgeItemNotFound",
    "KnowledgeRelationService",
    "KnowledgeService",
    "KnowledgeServiceError",
    "ProvenanceService",
    "RelationConflictError",
    "RelationNotFoundError",
]
