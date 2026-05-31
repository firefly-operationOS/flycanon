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

"""Cross-item conflict detection.

Two knowledge items can canonicalise contradicting statements
about the same topic -- legal team publishes a data-retention
policy v1, security team publishes a different one. The current
model has no way to notice. :class:`ConflictDetector` runs a
pass over the published-knowledge set to surface contradictions:

1. **Cluster.** Items sharing the same domain are pairwise cosine-
   similaried (over their version-body embeddings). Pairs with
   similarity >= ``min_similarity`` form candidates.
2. **Judge.** For each candidate pair, the answer model is asked
   a structured ``ConflictJudgment`` (``is_conflict`` + reasoning).
3. **Record.** Confirmed conflicts land as ``CandidateRow`` rows
   with ``metadata.kind=conflict_detection`` so the inbox UI
   queues them for human review alongside the standard candidate
   stream.

The detector is launched on demand via ``POST
/api/v1/knowledge:detect-conflicts`` -- v1 doesn't run it
automatically because the LLM cost is non-trivial.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from pydantic import BaseModel, Field
from pyfly.container import service

from flycanon.config import CanonSettings
from flycanon.core.agents import build_agent
from flycanon.core.services.audit import AuditService
from flycanon.core.services.embeddings import EmbeddingService
from flycanon.core.services.knowledge.relation_service import KnowledgeRelationService
from flycanon.interfaces.dtos.relation import CreateRelationRequest
from flycanon.interfaces.enums import RelationKind
from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)


class ConflictJudgment(BaseModel):
    """Structured output the judge LLM emits per candidate pair."""

    is_conflict: bool = Field(description="True when the two items make contradicting canonical claims.")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=2000)


class _ConflictPair(BaseModel):
    from_id: str
    to_id: str
    similarity: float


@service
class ConflictDetector:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        candidate_repository: CandidateRepository,
        embeddings: EmbeddingService,
        audit: AuditService,
        settings: CanonSettings,
        relations: KnowledgeRelationService | None = None,
    ) -> None:
        self._knowledge = knowledge_repository
        self._candidates = candidate_repository
        self._embeddings = embeddings
        self._audit = audit
        self._settings = settings
        # Optional: when wired, the detector also materialises a
        # ``conflicts_with`` relation alongside the CandidateRow so
        # the knowledge graph + provenance views surface the link
        # immediately, not after a human accepts the candidate.
        self._relations = relations

    async def detect(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        domain: str | None = None,
        min_similarity: float = 0.85,
        max_items: int = 50,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a full detector pass and return a summary.

        Returns:
            ``{pairs_evaluated, conflicts_found, candidate_ids}``.
        """
        items, _total = await self._knowledge.list_items(
            statuses=["published"],
            domains=[domain] if domain else None,
            limit=max_items,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if len(items) < 2:
            return {
                "pairs_evaluated": 0,
                "conflicts_found": 0,
                "candidate_ids": [],
            }

        # Embed every item body once -- O(N) calls, not O(N^2).
        embeddings_by_id: dict[str, list[float]] = {}
        for item in items:
            version = await self._knowledge.get_version(
                item.id,
                item.current_version,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if version is None or not version.body:
                continue
            try:
                embeddings_by_id[item.id] = await self._embeddings.embed_one(version.body[:8000])
            except Exception as exc:  # noqa: BLE001
                logger.warning("conflict: failed to embed %s: %s", item.id, exc)

        # Pairwise compare to find candidate conflict pairs.
        pairs: list[_ConflictPair] = []
        ids = list(embeddings_by_id.keys())
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                sim = _cosine(embeddings_by_id[a], embeddings_by_id[b])
                if sim >= min_similarity:
                    pairs.append(_ConflictPair(from_id=a, to_id=b, similarity=sim))

        # Judge each pair through the LLM.
        items_by_id = {item.id: item for item in items}
        conflicts_found = 0
        candidate_ids: list[str] = []
        relation_ids: list[str] = []
        for pair in pairs:
            judgment = await self._judge(items_by_id[pair.from_id], items_by_id[pair.to_id])
            if judgment is None or not judgment.is_conflict:
                continue
            # Dedup: don't queue a new CandidateRow when a previous
            # detection pass already proposed the same pair and the
            # human reviewer hasn't acted on it yet. Repeated scans
            # would otherwise fill the inbox with duplicate proposals.
            existing = await self._candidates.find_conflict_candidate(
                from_item_id=pair.from_id,
                to_item_id=pair.to_id,
            )
            if existing is not None:
                logger.info(
                    "conflict scan dedup: candidate %s already proposed for %s <-> %s",
                    existing.id,
                    pair.from_id,
                    pair.to_id,
                )
                candidate_ids.append(existing.id)
                # No conflicts_found bump -- we didn't actually find a
                # NEW conflict, the previous pass already accounted for
                # this pair.
                continue
            row = await self._record_candidate(
                pair=pair,
                judgment=judgment,
                from_item=items_by_id[pair.from_id],
                to_item=items_by_id[pair.to_id],
                actor=actor,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            candidate_ids.append(row.id)
            conflicts_found += 1
            # Also materialise the conflicts_with edge so the
            # knowledge graph + provenance views surface the link
            # immediately. We swallow the conflict-exists case --
            # the relation already covers it.
            if self._relations is not None:
                rel = await self._link_relation(
                    pair=pair,
                    judgment=judgment,
                    actor=actor,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                if rel is not None:
                    relation_ids.append(rel)

        await self._audit.record(
            event_type="knowledge.conflict_scan",
            subject_kind="knowledge",
            subject_id="*",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "domain": domain,
                "pairs_evaluated": len(pairs),
                "conflicts_found": conflicts_found,
            },
        )
        return {
            "pairs_evaluated": len(pairs),
            "conflicts_found": conflicts_found,
            "candidate_ids": candidate_ids,
            "relation_ids": relation_ids,
        }

    async def _judge(
        self,
        a: KnowledgeItemRow,
        b: KnowledgeItemRow,
    ) -> ConflictJudgment | None:
        agent = build_agent(
            name="flycanon-conflict-judge",
            model=self._settings.answer_model,
            output_type=ConflictJudgment,
            instructions=(
                "You are a strict canonical-knowledge auditor. Given two "
                "knowledge items on the same topic, decide whether they "
                "make CONTRADICTING canonical claims. Two items that say "
                "the same thing in different words are NOT a conflict; "
                "two items that prescribe different actions in the same "
                "scenario ARE. Emit ``is_conflict=true`` only when you're "
                "confident; the human reviewer's time is limited."
            ),
            settings=self._settings,
            max_output_tokens=1024,
        )
        prompt = (
            f"Item A (id={a.id}):\nTitle: {a.title}\n"
            f"Domain: {a.domain}\nSummary: {a.summary or ''}\n\n"
            f"Item B (id={b.id}):\nTitle: {b.title}\n"
            f"Domain: {b.domain}\nSummary: {b.summary or ''}\n\n"
            "Decide whether A and B contradict each other and reply as JSON."
        )
        try:
            result = await agent.run(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("conflict judge failed for %s/%s: %s", a.id, b.id, exc)
            return None
        output: Any = getattr(result, "output", result)
        if isinstance(output, ConflictJudgment):
            return output
        if isinstance(output, dict):
            return ConflictJudgment.model_validate(output)
        return None

    async def _record_candidate(
        self,
        *,
        pair: _ConflictPair,
        judgment: ConflictJudgment,
        from_item: KnowledgeItemRow,
        to_item: KnowledgeItemRow,
        actor: str | None,
        tenant_id: str,
        workspace_id: str,
    ) -> CandidateRow:
        row = CandidateRow(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            status="proposed",
            source_id=from_item.id,  # reuse the source slot to anchor
            title=f"Conflict: {from_item.title} vs {to_item.title}",
            summary=judgment.reasoning[:2000] or None,
            body=(
                f"Detector flagged a conflict between knowledge items "
                f"{from_item.id} and {to_item.id} "
                f"(similarity={pair.similarity:.3f}, confidence="
                f"{judgment.confidence:.3f}).\n\nReasoning:\n"
                f"{judgment.reasoning}"
            ),
            domain=from_item.domain,
            jurisdiction=from_item.jurisdiction,
            tags_json=["conflict_detection"],
            citations_json=[],
            score=judgment.confidence,
            rationale=judgment.reasoning[:2000] or None,
            actor=actor,
            metadata_json={
                "kind": "conflict_detection",
                "from_item_id": from_item.id,
                "to_item_id": to_item.id,
                "similarity": pair.similarity,
            },
        )
        stored_rows = await self._candidates.add_many([row])
        return stored_rows[0]

    async def _link_relation(
        self,
        *,
        pair: _ConflictPair,
        judgment: ConflictJudgment,
        actor: str | None,
        tenant_id: str,
        workspace_id: str,
    ) -> str | None:
        """Materialise the ``conflicts_with`` edge for the pair.

        Returns the relation id, or ``None`` on any failure (including
        the "relation already exists" case -- a prior scan or human
        edit may have linked the items already, and that's fine: the
        existing edge already carries the meaning we wanted to record).
        """
        if self._relations is None:
            return None
        try:
            row = await self._relations.add(
                pair.from_id,
                CreateRelationRequest(
                    to_item_id=pair.to_id,
                    kind=RelationKind.conflicts_with,
                    note=(judgment.reasoning or "")[:2000] or None,
                    actor=actor,
                ),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            return row.id
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "conflict relation skipped from=%s to=%s: %s",
                pair.from_id,
                pair.to_id,
                exc,
            )
            return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
