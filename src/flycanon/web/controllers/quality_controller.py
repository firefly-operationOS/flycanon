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

"""Knowledge-quality endpoints: stale + conflict scans."""

from __future__ import annotations

from pydantic import BaseModel, Field
from pyfly.container import rest_controller
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, Valid, get_mapping, post_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.quality import ConflictDetector, StaleDetector
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.web.conventions import TenantContext, tenant_context_from_request


class ConflictScanRequest(BaseModel):
    """``POST /api/v1/knowledge:detect-conflicts`` body.

    ``actor`` is intentionally NOT a body field: the audit actor is
    derived from the request context (the JWT subject or agent-token
    prefix).
    """

    domain: str | None = Field(default=None)
    min_similarity: float = Field(default=0.85, ge=0.5, le=1.0)
    max_items: int = Field(default=50, ge=2, le=500)


class ConflictScanResponse(BaseModel):
    pairs_evaluated: int = Field(ge=0)
    conflicts_found: int = Field(ge=0)
    candidate_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Knowledge-graph ``conflicts_with`` edge ids materialised "
            "alongside the inbox candidates. Empty when the detector "
            "is wired without a KnowledgeRelationService binding."
        ),
    )


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
    async def stale_report(self, http_request: Request) -> StaleReport:
        """Return staleness scores for every published knowledge item.

        Score is on-demand + cached on
        ``KnowledgeItemRow.metadata_json.staleness`` (6h TTL). First
        call populates; subsequent calls within the TTL return the
        cached value.

        ``score = 1 - max(cosine(version_body, recent_source))``.
        High = the canon disagrees with fresh sources.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        items, _total = await self._knowledge.list_items(
            statuses=["published"],
            limit=200,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
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
        http_request: Request,
        request: Valid[Body[ConflictScanRequest]],
    ) -> ConflictScanResponse:
        """Run the conflict-detection pass and queue any contradictions.

        Confirmed conflicts land as ``CandidateRow``s with
        ``metadata.kind=conflict_detection`` so the inbox UI's
        existing candidate queue surfaces them alongside ordinary
        proposals.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        result = await self._conflicts.detect(
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            domain=request.domain,
            min_similarity=request.min_similarity,
            max_items=request.max_items,
            actor=ctx.actor,
            correlation_id=get_correlation_id(),
        )
        return ConflictScanResponse(
            pairs_evaluated=int(result.get("pairs_evaluated", 0)),
            conflicts_found=int(result.get("conflicts_found", 0)),
            candidate_ids=list(result.get("candidate_ids", []) or []),
            relation_ids=list(result.get("relation_ids", []) or []),
        )
