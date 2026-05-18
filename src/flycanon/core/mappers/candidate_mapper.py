# Copyright 2026 Firefly Software Solutions Inc
"""``CandidateRow`` -> :class:`CandidateRecord` DTO."""

from __future__ import annotations

from flycanon.interfaces.dtos.candidate import CandidateRecord
from flycanon.interfaces.dtos.knowledge import Citation
from flycanon.interfaces.enums import CandidateStatus, Domain, Jurisdiction
from flycanon.models.entities.candidate import CandidateRow


def to_candidate_record(row: CandidateRow) -> CandidateRecord:
    citations = [
        Citation(
            source_id=row.source_id,
            chunk_id=raw.get("chunk_id"),
            quote=raw.get("quote"),
            relevance=raw.get("relevance"),
        )
        for raw in (row.citations_json or [])
    ]
    return CandidateRecord(
        id=row.id,
        status=CandidateStatus(row.status),
        source_id=row.source_id,
        title=row.title,
        summary=row.summary,
        body=row.body,
        domain=Domain(row.domain),
        jurisdiction=Jurisdiction(row.jurisdiction),
        tags=list(row.tags_json or []),
        citations=citations,
        score=row.score,
        rationale=row.rationale,
        materialised_knowledge_item_id=row.materialised_knowledge_item_id,
        materialised_version=row.materialised_version,
        actor=row.actor,
        created_at=row.created_at,
        decided_at=row.decided_at,
        metadata=dict(row.metadata_json or {}),
    )
