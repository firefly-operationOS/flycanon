# Copyright 2026 Firefly Software Solutions Inc
"""SQLAlchemy ORM entities.

Every persistent type in flycanon lives here. The shared declarative
:class:`Base` is imported by Alembic's ``env.py`` to populate
``target_metadata``; ``alembic upgrade head`` discovers tables by
metadata, not by walking modules.
"""

from __future__ import annotations

from flycanon.models.entities.audit_event import AuditEventRow
from flycanon.models.entities.base import Base
from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow
from flycanon.models.entities.taxonomy_node import TaxonomyNodeRow

__all__ = [
    "AuditEventRow",
    "Base",
    "CandidateRow",
    "CitationRow",
    "KnowledgeChunkRow",
    "KnowledgeItemRow",
    "KnowledgeVersionRow",
    "SourceRow",
    "TaxonomyNodeRow",
]
