# Copyright 2026 Firefly Software Solutions Inc
"""Public enums shipped on the wire.

The values are part of the API contract -- renaming a member is a
breaking change.
"""

from __future__ import annotations

from flycanon.interfaces.enums.candidate_status import CandidateStatus
from flycanon.interfaces.enums.domain import Domain
from flycanon.interfaces.enums.jurisdiction import Jurisdiction
from flycanon.interfaces.enums.knowledge_status import KnowledgeStatus
from flycanon.interfaces.enums.relation_kind import RelationKind
from flycanon.interfaces.enums.source_kind import SourceKind
from flycanon.interfaces.enums.source_status import SourceStatus

__all__ = [
    "CandidateStatus",
    "Domain",
    "Jurisdiction",
    "KnowledgeStatus",
    "RelationKind",
    "SourceKind",
    "SourceStatus",
]
