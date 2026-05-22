# Copyright 2026 Firefly Software Solutions Inc
"""Candidate persistence + accept / reject orchestration.

The :class:`CandidateService` materialises LLM proposals as
``canon_candidates`` rows and owns the accept / reject decisions:

* ``propose_from_source`` -- pull chunks, run the consolidator,
  persist the resulting candidates in ``proposed`` status, emit a
  ``CandidateProposed`` event per row.
* ``accept`` -- materialise the candidate as a new
  :class:`KnowledgeVersionRow` via :class:`KnowledgeService`. Flip
  status to ``accepted`` and record materialisation pointers.
* ``reject`` -- flip status to ``rejected`` with the operator's reason.
"""

from __future__ import annotations

import logging
from typing import Any

from pyfly.container import service
from pyfly.eda import EventPublisher

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.consolidation.consolidator import (
    CandidateProposal,
    Consolidator,
)
from flycanon.core.services.consolidation.errors import (
    CandidateAlreadyDecided,
    CandidateNotFound,
)
from flycanon.core.services.knowledge import KnowledgeService
from flycanon.interfaces.dtos.candidate import (
    AcceptCandidateRequest,
    ProposeCandidateRequest,
    RejectCandidateRequest,
)
from flycanon.interfaces.dtos.knowledge import (
    Citation,
    CreateKnowledgeRequest,
    UpdateKnowledgeRequest,
)
from flycanon.interfaces.enums import CandidateStatus, Domain, Jurisdiction
from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


@service
class CandidateService:
    def __init__(
        self,
        consolidator: Consolidator,
        candidate_repository: CandidateRepository,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        knowledge: KnowledgeService,
        audit: AuditService,
        event_publisher: EventPublisher,
        settings: CanonSettings,
    ) -> None:
        self._consolidator = consolidator
        self._candidates = candidate_repository
        self._sources = source_repository
        self._chunks = chunk_repository
        self._knowledge = knowledge
        self._audit = audit
        self._publisher = event_publisher
        self._settings = settings

    # ------------------------------------------------------------------
    # Propose
    # ------------------------------------------------------------------

    async def propose_from_source(
        self,
        request: ProposeCandidateRequest,
        *,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str | None = None,
    ) -> list[CandidateRow]:
        source = await self._sources.get(
            request.source_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if source is None:
            from flycanon.core.services.consolidation.errors import ConsolidationError
            from flycanon.models.entities.source import SourceRow as _SourceRow  # noqa: F401

            raise ConsolidationError(f"source {request.source_id!r} not found")
        chunks = await self._chunks.list_for_source(request.source_id)
        chunks_window = chunks[: request.max_chunks]

        output = await self._consolidator.propose(
            source=source,
            chunks=chunks_window,
            domain_hint=request.domain,
            jurisdiction_hint=request.jurisdiction,
            extra_instructions=request.instructions,
        )

        rows = [
            _proposal_to_row(
                source=source,
                proposal=proposal,
                actor=request.actor,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            for proposal in output.candidates
        ]
        stored = await self._candidates.add_many(rows)
        for row in stored:
            await self._audit.record(
                event_type="candidate.proposed",
                subject_kind="candidate",
                subject_id=row.id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor=request.actor,
                correlation_id=correlation_id,
                payload={
                    "source_id": row.source_id,
                    "domain": row.domain,
                    "score": row.score,
                },
            )
            await self._publish(
                event_type=self._settings.candidate_proposed_event,
                payload={
                    "candidate_id": row.id,
                    "source_id": row.source_id,
                    "domain": row.domain,
                    "score": row.score,
                },
            )
        logger.info("candidates proposed source=%s count=%d", request.source_id, len(stored))
        return stored

    # ------------------------------------------------------------------
    # Accept / reject
    # ------------------------------------------------------------------

    async def accept(
        self,
        candidate_id: str,
        request: AcceptCandidateRequest,
        *,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str | None = None,
    ) -> CandidateRow:
        # Atomic claim BEFORE the LLM-heavy / knowledge-item write so
        # two operators clicking accept in the inbox can't both
        # materialise the same proposal twice. The previous
        # check-then-act pattern would let both threads pass
        # ``status == 'proposed'`` and both write a knowledge item.
        existing = await self._candidates.get(
            candidate_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if existing is None:
            raise CandidateNotFound(candidate_id)
        candidate = await self._candidates.claim_decision(
            candidate_id,
            new_status=CandidateStatus.accepted.value,
            actor=request.actor,
        )
        if candidate is None:
            # The row exists but is no longer ``proposed`` -- another
            # operator already accepted / rejected it.
            raise CandidateAlreadyDecided(candidate_id, existing.status)

        citations = await self._citations_from_candidate(candidate)
        domain = Domain(candidate.domain)
        jurisdiction = Jurisdiction(candidate.jurisdiction)

        if request.target_item_id is None:
            stored_version = await self._knowledge.create(
                CreateKnowledgeRequest(
                    title=candidate.title,
                    body=candidate.body,
                    summary=candidate.summary,
                    domain=domain,
                    jurisdiction=jurisdiction,
                    tags=list(candidate.tags_json or []),
                    citations=citations,
                    publish=request.publish,
                    actor=request.actor,
                    metadata={"originating_candidate_id": candidate.id},
                ),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                originating_candidate_id=candidate.id,
                correlation_id=correlation_id,
            )
            target_item_id = stored_version.knowledge_item_id
            new_version = stored_version.version
        else:
            stored_version = await self._knowledge.update(
                request.target_item_id,
                UpdateKnowledgeRequest(
                    title=candidate.title,
                    body=candidate.body,
                    summary=candidate.summary,
                    domain=domain,
                    jurisdiction=jurisdiction,
                    tags=list(candidate.tags_json or []),
                    citations=citations,
                    publish=request.publish,
                    actor=request.actor,
                    metadata={"originating_candidate_id": candidate.id},
                ),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                originating_candidate_id=candidate.id,
                correlation_id=correlation_id,
            )
            target_item_id = stored_version.knowledge_item_id
            new_version = stored_version.version

        stored = await self._candidates.finalise(
            candidate_id,
            materialised_knowledge_item_id=target_item_id,
            materialised_version=new_version,
        )
        if stored is None:  # defensive -- the claim above already pinned the row
            stored = candidate

        await self._audit.record(
            event_type="candidate.accepted",
            subject_kind="candidate",
            subject_id=candidate_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={
                "materialised_knowledge_item_id": target_item_id,
                "materialised_version": new_version,
            },
        )
        await self._publish(
            event_type=self._settings.candidate_accepted_event,
            payload={
                "candidate_id": candidate_id,
                "materialised_knowledge_item_id": target_item_id,
                "materialised_version": new_version,
            },
        )
        return stored

    async def reject(
        self,
        candidate_id: str,
        request: RejectCandidateRequest,
        *,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str | None = None,
    ) -> CandidateRow:
        existing = await self._candidates.get(
            candidate_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if existing is None:
            raise CandidateNotFound(candidate_id)
        candidate = await self._candidates.claim_decision(
            candidate_id,
            new_status=CandidateStatus.rejected.value,
            actor=request.actor,
        )
        if candidate is None:
            raise CandidateAlreadyDecided(candidate_id, existing.status)

        stored = await self._candidates.finalise(
            candidate_id,
            metadata_patch={"rejection_reason": request.reason},
        )
        if stored is None:
            stored = candidate

        await self._audit.record(
            event_type="candidate.rejected",
            subject_kind="candidate",
            subject_id=candidate_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={"reason": request.reason},
        )
        await self._publish(
            event_type=self._settings.candidate_rejected_event,
            payload={"candidate_id": candidate_id, "reason": request.reason},
        )
        return stored

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _publish(self, *, event_type: str, payload: dict[str, Any]) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.knowledge_topic,
                event_type=event_type,
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "candidate publish failed event=%s payload_keys=%s: %s",
                event_type,
                sorted(payload.keys()),
                exc,
            )

    async def _citations_from_candidate(self, candidate: CandidateRow) -> list[Citation]:
        """Hydrate the candidate's citations into ``Citation`` DTOs.

        The candidate's ``citations_json`` only stores
        ``chunk_id`` / ``quote`` / ``relevance`` (per the consolidator
        output shape). We resolve ``page`` here by batching a single
        lookup against :class:`ChunkRepository` so the materialised
        ``KnowledgeVersion`` carries the same page-level breadcrumb
        the retrieval Hit / provenance graph expose. Without this
        step the citation table's ``page`` column ends up NULL even
        when the source chunk has a known page.
        """
        raw_citations = candidate.citations_json or []
        chunk_ids: list[str] = sorted({cid for raw in raw_citations if (cid := raw.get("chunk_id"))})
        page_by_chunk: dict[str, int | None] = {}
        if chunk_ids:
            chunk_rows = await self._chunks.get_many(chunk_ids)
            for row in chunk_rows:
                page_by_chunk[row.id] = row.page
        citations: list[Citation] = []
        for raw in raw_citations:
            chunk_id = raw.get("chunk_id")
            citations.append(
                Citation(
                    source_id=candidate.source_id,
                    chunk_id=chunk_id,
                    quote=raw.get("quote"),
                    relevance=raw.get("relevance"),
                    page=page_by_chunk.get(chunk_id) if chunk_id else None,
                )
            )
        return citations


def _proposal_to_row(
    *,
    source: SourceRow,
    proposal: CandidateProposal,
    actor: str | None,
    tenant_id: str,
    workspace_id: str,
) -> CandidateRow:
    citations_json = [
        {
            "chunk_id": citation.chunk_id,
            "quote": citation.quote,
            "relevance": citation.relevance,
        }
        for citation in proposal.citations
    ]
    return CandidateRow(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status=CandidateStatus.proposed.value,
        source_id=source.id,
        title=proposal.title,
        summary=proposal.summary,
        body=proposal.body,
        domain=proposal.domain.value,
        jurisdiction=proposal.jurisdiction.value,
        tags_json=list(proposal.tags),
        citations_json=citations_json,
        score=proposal.score,
        rationale=proposal.rationale,
        actor=actor,
    )
