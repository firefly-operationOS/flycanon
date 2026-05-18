# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge entity -> public DTO mappers."""

from __future__ import annotations

from flycanon.interfaces.dtos.knowledge import (
    Citation,
    KnowledgeItem,
    KnowledgeVersion,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow


def to_knowledge_item(row: KnowledgeItemRow) -> KnowledgeItem:
    return KnowledgeItem(
        id=row.id,
        status=KnowledgeStatus(row.status),
        current_version=row.current_version,
        title=row.title,
        summary=row.summary,
        domain=Domain(row.domain),
        jurisdiction=Jurisdiction(row.jurisdiction),
        tags=list(row.tags_json or []),
        created_at=row.created_at,
        updated_at=row.updated_at,
        retired_at=row.retired_at,
    )


def to_knowledge_version(
    row: KnowledgeVersionRow,
    *,
    citations: list[CitationRow] | None = None,
) -> KnowledgeVersion:
    return KnowledgeVersion(
        knowledge_item_id=row.knowledge_item_id,
        version=row.version,
        status=KnowledgeStatus(row.status),
        title=row.title,
        summary=row.summary,
        body=row.body,
        domain=Domain(row.domain),
        jurisdiction=Jurisdiction(row.jurisdiction),
        tags=list(row.tags_json or []),
        citations=[to_citation(c) for c in (citations or [])],
        supersedes_version=row.supersedes_version,
        superseded_by_version=row.superseded_by_version,
        created_by=row.created_by,
        created_at=row.created_at,
        metadata=dict(row.metadata_json or {}),
    )


def to_citation(row: CitationRow) -> Citation:
    return Citation(
        source_id=row.source_id,
        chunk_id=row.chunk_id,
        quote=row.quote,
        relevance=row.relevance,
        page=row.page,
    )
