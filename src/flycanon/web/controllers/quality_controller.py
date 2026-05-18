# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge-quality endpoints: stale + conflict scans."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pyfly.container import rest_controller
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, Valid, get_mapping, post_mapping, request_mapping

from flycanon.core.services.quality import ConflictDetector, StaleDetector
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository


class ConflictScanRequest(BaseModel):
    domain: str | None = Field(default=None)
    min_similarity: float = Field(default=0.85, ge=0.5, le=1.0)
    max_items: int = Field(default=50, ge=2, le=500)
    actor: str | None = Field(default=None)


class ConflictScanResponse(BaseModel):
    pairs_evaluated: int = Field(ge=0)
    conflicts_found: int = Field(ge=0)
    candidate_ids: list[str] = Field(default_factory=list)


class StaleItem(BaseModel):
    knowledge_item_id: str
    title: str
    domain: str
    score: float | None = Field(default=None)
    max_similarity: float | None = Field(default=None)
    sample_size: int = Field(ge=0)
    computed_at: str


class StaleReport(BaseModel):
    items: list[StaleItem] = Field(default_factory=list)
    total: int = Field(ge=0)


@rest_controller
@request_mapping("/api/v1/knowledge")
class KnowledgeQualityController:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        stale: StaleDetector,
        conflicts: ConflictDetector,
    ) -> None:
        self._knowledge = knowledge_repository
        self._stale = stale
        self._conflicts = conflicts

    @get_mapping(":stale")
    async def stale_report(self) -> StaleReport:
        """Return staleness scores for every published knowledge item.

        Score is on-demand + cached on
        ``KnowledgeItemRow.metadata_json.staleness`` (6h TTL). First
        call populates; subsequent calls within the TTL return the
        cached value.

        ``score = 1 - max(cosine(version_body, recent_source))``.
        High = the canon disagrees with fresh sources.
        """
        items, _total = await self._knowledge.list_items(
            statuses=["published"], limit=200
        )
        rows: list[StaleItem] = []
        for item in items:
            stale = await self._stale.score(item)
            rows.append(
                StaleItem(
                    knowledge_item_id=item.id,
                    title=item.title,
                    domain=item.domain,
                    score=stale.get("score"),
                    max_similarity=stale.get("max_similarity"),
                    sample_size=int(stale.get("sample_size") or 0),
                    computed_at=str(stale.get("computed_at") or ""),
                )
            )
        return StaleReport(items=rows, total=len(rows))

    @post_mapping(":detect-conflicts")
    async def detect_conflicts(
        self,
        request: Valid[Body[ConflictScanRequest]],
    ) -> ConflictScanResponse:
        """Run the conflict-detection pass and queue any contradictions.

        Confirmed conflicts land as ``CandidateRow``s with
        ``metadata.kind=conflict_detection`` so the inbox UI's
        existing candidate queue surfaces them alongside ordinary
        proposals.
        """
        result = await self._conflicts.detect(
            domain=request.domain,
            min_similarity=request.min_similarity,
            max_items=request.max_items,
            actor=request.actor,
            correlation_id=get_correlation_id(),
        )
        return ConflictScanResponse(
            pairs_evaluated=int(result.get("pairs_evaluated", 0)),
            conflicts_found=int(result.get("conflicts_found", 0)),
            candidate_ids=list(result.get("candidate_ids", []) or []),
        )
