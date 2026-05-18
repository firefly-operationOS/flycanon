# Copyright 2026 Firefly Software Solutions Inc
"""Relation entity -> public DTO mapper."""

from __future__ import annotations

from flycanon.interfaces.dtos.relation import KnowledgeRelation
from flycanon.interfaces.enums import RelationKind
from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow


def to_knowledge_relation(row: KnowledgeRelationRow) -> KnowledgeRelation:
    return KnowledgeRelation(
        id=row.id,
        from_item_id=row.from_item_id,
        to_item_id=row.to_item_id,
        kind=RelationKind(row.kind),
        since_version=row.since_version,
        note=row.note,
        actor=row.actor,
        created_at=row.created_at,
    )
