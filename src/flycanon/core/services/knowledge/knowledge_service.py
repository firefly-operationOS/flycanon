# Copyright 2026 Firefly Software Solutions Inc
"""Canonical knowledge-item lifecycle.

The service owns the full state machine: create, update, supersede,
retire. Every transition appends a row to ``canon_knowledge_versions``
(never updates one in place), updates the pointer on
``canon_knowledge_items``, records an audit row through
:class:`AuditService`, and publishes the lifecycle event on
``flycanon.knowledge`` for downstream consumers.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.knowledge.errors import (
    InvalidSupersedeTarget,
    KnowledgeItemAlreadyRetired,
    KnowledgeItemNotFound,
)
from flycanon.interfaces.dtos.knowledge import (
    Citation,
    CreateKnowledgeRequest,
    RetireKnowledgeRequest,
    SupersedeKnowledgeRequest,
    UpdateKnowledgeRequest,
)
from flycanon.interfaces.enums import KnowledgeStatus
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Owns the canonical-item lifecycle.

    Each public method runs to completion or raises; partial states
    are avoided by ordering the writes (version first, citations
    next, item pointer last) so a failure mid-flow leaves the most
    recent published version still pointing at valid data.
    """

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        audit: AuditService,
        event_publisher: object | None,
        settings: CanonSettings,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._publisher = event_publisher
        self._settings = settings

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        request: CreateKnowledgeRequest,
        *,
        originating_candidate_id: str | None = None,
        correlation_id: str | None = None,
    ) -> KnowledgeVersionRow:
        """Materialise a new knowledge item + version=1."""
        item_id = str(uuid.uuid4())
        status = KnowledgeStatus.published if request.publish else KnowledgeStatus.draft

        item = KnowledgeItemRow(
            id=item_id,
            status=status.value,
            current_version=1,
            title=request.title,
            summary=request.summary,
            domain=request.domain.value,
            jurisdiction=request.jurisdiction.value,
            tags_json=list(request.tags),
            metadata_json=dict(request.metadata),
        )
        version = KnowledgeVersionRow(
            knowledge_item_id=item_id,
            version=1,
            status=status.value,
            title=request.title,
            summary=request.summary,
            body=request.body,
            domain=request.domain.value,
            jurisdiction=request.jurisdiction.value,
            tags_json=list(request.tags),
            originating_candidate_id=originating_candidate_id,
            created_by=request.actor,
            metadata_json=dict(request.metadata),
        )
        await self._repository.upsert_item(item)
        stored_version = await self._repository.add_version(version)
        await self._repository.add_citations(_citations_for(stored_version.id, request.citations))

        await self._audit.record(
            event_type=f"knowledge.{status.value}",
            subject_kind="knowledge_item",
            subject_id=item_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={"version": 1, "domain": request.domain.value, "title": request.title},
        )
        await self._publish_lifecycle(
            event_type=self._settings.knowledge_published_event if request.publish else "KnowledgeItemDrafted",
            item_id=item_id,
            version=1,
            payload={"title": request.title, "domain": request.domain.value, "status": status.value},
        )
        logger.info("knowledge created id=%s version=1 status=%s", item_id, status.value)
        return stored_version

    # ------------------------------------------------------------------
    # Update -- append a new version
    # ------------------------------------------------------------------

    async def update(
        self,
        item_id: str,
        request: UpdateKnowledgeRequest,
        *,
        originating_candidate_id: str | None = None,
        correlation_id: str | None = None,
    ) -> KnowledgeVersionRow:
        item = await self._repository.get_item(item_id)
        if item is None:
            raise KnowledgeItemNotFound(item_id)
        if item.status == KnowledgeStatus.retired.value:
            raise KnowledgeItemAlreadyRetired(item_id)

        current = await self._repository.get_version(item_id, item.current_version)
        if current is None:
            raise KnowledgeItemNotFound(item_id)

        new_version = item.current_version + 1
        new_status = (
            KnowledgeStatus.published if request.publish else KnowledgeStatus.draft
        )
        new = KnowledgeVersionRow(
            knowledge_item_id=item_id,
            version=new_version,
            status=new_status.value,
            title=request.title or current.title,
            summary=request.summary if request.summary is not None else current.summary,
            body=request.body or current.body,
            domain=(request.domain.value if request.domain else current.domain),
            jurisdiction=(
                request.jurisdiction.value if request.jurisdiction else current.jurisdiction
            ),
            tags_json=list(request.tags) if request.tags is not None else list(current.tags_json or []),
            supersedes_version=current.version,
            originating_candidate_id=originating_candidate_id,
            created_by=request.actor,
            metadata_json=(
                dict(request.metadata) if request.metadata is not None else dict(current.metadata_json or {})
            ),
        )
        stored_version = await self._repository.add_version(new)
        citations = request.citations if request.citations is not None else []
        await self._repository.add_citations(_citations_for(stored_version.id, citations))

        # Update the pointers: bump the item, mark the previous version
        # as superseded.
        item.current_version = new_version
        item.status = new_status.value
        item.title = new.title
        item.summary = new.summary
        item.domain = new.domain
        item.jurisdiction = new.jurisdiction
        item.tags_json = list(new.tags_json or [])
        item.updated_at = datetime.now(UTC)
        await self._repository.upsert_item(item)

        current.status = KnowledgeStatus.superseded.value
        current.superseded_by_version = new_version
        await self._repository.upsert_version_status(current)

        await self._audit.record(
            event_type=f"knowledge.updated",
            subject_kind="knowledge_item",
            subject_id=item_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={
                "version": new_version,
                "previous_version": current.version,
                "status": new_status.value,
            },
        )
        await self._publish_lifecycle(
            event_type=(
                self._settings.knowledge_published_event
                if request.publish
                else "KnowledgeItemDrafted"
            ),
            item_id=item_id,
            version=new_version,
            payload={"previous_version": current.version, "status": new_status.value},
        )
        logger.info(
            "knowledge updated id=%s version=%s previous=%s status=%s",
            item_id,
            new_version,
            current.version,
            new_status.value,
        )
        return stored_version

    # ------------------------------------------------------------------
    # Supersede -- point at a different item entirely
    # ------------------------------------------------------------------

    async def supersede(
        self,
        item_id: str,
        request: SupersedeKnowledgeRequest,
        *,
        correlation_id: str | None = None,
    ) -> KnowledgeItemRow:
        item = await self._repository.get_item(item_id)
        if item is None:
            raise KnowledgeItemNotFound(item_id)
        if item_id == request.superseded_by_item_id:
            raise InvalidSupersedeTarget(item_id, request.superseded_by_item_id, "self-supersedure")
        target = await self._repository.get_item(request.superseded_by_item_id)
        if target is None:
            raise InvalidSupersedeTarget(item_id, request.superseded_by_item_id, "target not found")
        if target.status == KnowledgeStatus.retired.value:
            raise InvalidSupersedeTarget(item_id, request.superseded_by_item_id, "target is retired")

        item.status = KnowledgeStatus.superseded.value
        item.superseded_by_item_id = request.superseded_by_item_id
        item.updated_at = datetime.now(UTC)
        stored = await self._repository.upsert_item(item)

        await self._audit.record(
            event_type="knowledge.superseded",
            subject_kind="knowledge_item",
            subject_id=item_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={
                "superseded_by_item_id": request.superseded_by_item_id,
                "reason": request.reason,
            },
        )
        await self._publish_lifecycle(
            event_type=self._settings.knowledge_superseded_event,
            item_id=item_id,
            version=stored.current_version,
            payload={"superseded_by_item_id": request.superseded_by_item_id},
        )
        logger.info(
            "knowledge superseded id=%s -> %s", item_id, request.superseded_by_item_id
        )
        return stored

    # ------------------------------------------------------------------
    # Retire -- final state
    # ------------------------------------------------------------------

    async def retire(
        self,
        item_id: str,
        request: RetireKnowledgeRequest,
        *,
        correlation_id: str | None = None,
    ) -> KnowledgeItemRow:
        item = await self._repository.get_item(item_id)
        if item is None:
            raise KnowledgeItemNotFound(item_id)
        if item.status == KnowledgeStatus.retired.value:
            raise KnowledgeItemAlreadyRetired(item_id)

        item.status = KnowledgeStatus.retired.value
        item.retired_at = datetime.now(UTC)
        item.retired_reason = request.reason
        item.updated_at = item.retired_at
        stored = await self._repository.upsert_item(item)

        await self._audit.record(
            event_type="knowledge.retired",
            subject_kind="knowledge_item",
            subject_id=item_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={"reason": request.reason},
        )
        await self._publish_lifecycle(
            event_type=self._settings.knowledge_retired_event,
            item_id=item_id,
            version=stored.current_version,
            payload={"reason": request.reason},
        )
        logger.info("knowledge retired id=%s", item_id)
        return stored

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def get(self, item_id: str) -> KnowledgeItemRow:
        item = await self._repository.get_item(item_id)
        if item is None:
            raise KnowledgeItemNotFound(item_id)
        return item

    async def list_versions(self, item_id: str) -> list[KnowledgeVersionRow]:
        return await self._repository.list_versions(item_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _publish_lifecycle(
        self,
        *,
        event_type: str,
        item_id: str,
        version: int,
        payload: dict[str, Any],
    ) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.knowledge_topic,
                event_type=event_type,
                payload={"item_id": item_id, "version": version, **payload},
            )
        except Exception as exc:
            logger.warning(
                "knowledge publish failed event=%s item_id=%s: %s",
                event_type,
                item_id,
                exc,
            )


def _citations_for(version_id: str, citations: Iterable[Citation]) -> list[CitationRow]:
    rows: list[CitationRow] = []
    for citation in citations:
        rows.append(
            CitationRow(
                knowledge_version_id=version_id,
                chunk_id=citation.chunk_id,
                source_id=citation.source_id,
                quote=citation.quote,
                relevance=citation.relevance,
                page=citation.page,
            )
        )
    return rows
