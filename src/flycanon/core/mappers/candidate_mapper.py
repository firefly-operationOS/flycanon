# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
