# Copyright 2026 Firefly Software Solutions Inc
"""Entity <-> public-DTO mappers.

Every public response shape is built by a function here; no
controller should hand a SQLAlchemy row to FastAPI directly. The
mappers also convert the JSON columns (``tags_json``, ``citations_json``)
into typed lists / DTOs so the wire shape is self-describing.
"""

from __future__ import annotations

from flycanon.core.mappers.audit_mapper import to_audit_event
from flycanon.core.mappers.candidate_mapper import to_candidate_record
from flycanon.core.mappers.knowledge_mapper import (
    to_citation,
    to_knowledge_item,
    to_knowledge_version,
)
from flycanon.core.mappers.relation_mapper import to_knowledge_relation
from flycanon.core.mappers.source_mapper import to_source_record
from flycanon.core.mappers.taxonomy_mapper import to_taxonomy_node

__all__ = [
    "to_audit_event",
    "to_candidate_record",
    "to_citation",
    "to_knowledge_item",
    "to_knowledge_relation",
    "to_knowledge_version",
    "to_source_record",
    "to_taxonomy_node",
]
