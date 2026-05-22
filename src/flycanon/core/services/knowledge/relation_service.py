# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge-relation service.

Owns the typed graph of knowledge_item -> knowledge_item edges
(``related`` / ``depends_on`` / ``conflicts_with`` / ``replaces``).
Every mutation is audited + announced on
``flycanon.knowledge`` so projections can keep their relation views
in sync.
"""

from __future__ import annotations

import logging

from pyfly.container import service
from pyfly.eda import EventPublisher
from sqlalchemy.exc import IntegrityError

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.knowledge.errors import KnowledgeItemNotFound
from flycanon.interfaces.dtos.relation import CreateRelationRequest
from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.relation_repository import RelationRepository
from flycanon.web.conventions import FireflyHTTPException

logger = logging.getLogger(__name__)


class RelationConflictError(FireflyHTTPException):
    """Same (from, to, kind) tuple already exists."""

    status = 409
    code = "relation_already_exists"
    title = "Relation already exists"
    http_status = 409

    def __init__(self, from_item_id: str, to_item_id: str, kind: str) -> None:
        super().__init__(f"relation already exists: {from_item_id} -[{kind}]-> {to_item_id}")


class InvalidRelationError(FireflyHTTPException):
    """Self-relations / unknown target."""

    status = 422
    code = "invalid_relation"
    title = "Invalid relation"
    http_status = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RelationNotFoundError(FireflyHTTPException):
    status = 404
    code = "relation_not_found"
    title = "Relation not found"
    http_status = 404

    def __init__(self, relation_id: str) -> None:
        super().__init__(f"relation {relation_id!r} not found")


@service
class KnowledgeRelationService:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        relation_repository: RelationRepository,
        audit: AuditService,
        event_publisher: EventPublisher,
        settings: CanonSettings,
    ) -> None:
        self._knowledge = knowledge_repository
        self._relations = relation_repository
        self._audit = audit
        self._publisher = event_publisher
        self._settings = settings

    async def add(
        self,
        from_item_id: str,
        request: CreateRelationRequest,
        *,
        tenant_id: str,
        workspace_id: str,
        correlation_id: str | None = None,
    ) -> KnowledgeRelationRow:
        if from_item_id == request.to_item_id:
            raise InvalidRelationError("from and to must be different knowledge items")

        from_item = await self._knowledge.get_item(from_item_id)
        if from_item is None:
            raise KnowledgeItemNotFound(from_item_id)
        to_item = await self._knowledge.get_item(request.to_item_id)
        if to_item is None:
            raise InvalidRelationError(f"to_item_id {request.to_item_id!r} does not exist")

        row = KnowledgeRelationRow(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            from_item_id=from_item_id,
            to_item_id=request.to_item_id,
            kind=request.kind.value,
            since_version=request.since_version,
            note=request.note,
            actor=request.actor,
        )
        try:
            stored = await self._relations.add(row)
        except IntegrityError as exc:
            # Unique-constraint violation on (from, to, kind). Both
            # Postgres + SQLite raise IntegrityError for this; we no
            # longer have to substring-match the engine's text.
            raise RelationConflictError(from_item_id, request.to_item_id, request.kind.value) from exc

        await self._audit.record(
            event_type="knowledge.relation_added",
            subject_kind="knowledge_item",
            subject_id=from_item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=request.actor,
            correlation_id=correlation_id,
            payload={
                "relation_id": stored.id,
                "to_item_id": request.to_item_id,
                "kind": request.kind.value,
            },
        )
        await self._publish(
            "KnowledgeRelationAdded",
            payload={
                "relation_id": stored.id,
                "from_item_id": from_item_id,
                "to_item_id": request.to_item_id,
                "kind": request.kind.value,
            },
        )
        logger.info(
            "knowledge.relation_added id=%s from=%s to=%s kind=%s",
            stored.id,
            from_item_id,
            request.to_item_id,
            request.kind.value,
        )
        return stored

    async def remove(
        self,
        relation_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        relation = await self._relations.get(relation_id)
        if relation is None:
            raise RelationNotFoundError(relation_id)
        deleted = await self._relations.delete(relation_id)
        if not deleted:
            raise RelationNotFoundError(relation_id)
        await self._audit.record(
            event_type="knowledge.relation_removed",
            subject_kind="knowledge_item",
            subject_id=relation.from_item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "relation_id": relation_id,
                "to_item_id": relation.to_item_id,
                "kind": relation.kind,
            },
        )
        await self._publish(
            "KnowledgeRelationRemoved",
            payload={
                "relation_id": relation_id,
                "from_item_id": relation.from_item_id,
                "to_item_id": relation.to_item_id,
                "kind": relation.kind,
            },
        )

    async def list_for_item(self, item_id: str) -> tuple[list, list]:
        """Return ``(outgoing, incoming)`` rows for the item."""
        item = await self._knowledge.get_item(item_id)
        if item is None:
            raise KnowledgeItemNotFound(item_id)
        outgoing = await self._relations.list_for_item(item_id, direction="out")
        incoming = await self._relations.list_for_item(item_id, direction="in")
        return outgoing, incoming

    async def _publish(self, event_type: str, *, payload: dict) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.knowledge_topic,
                event_type=event_type,
                payload=payload,
            )
        except Exception as exc:
            logger.warning("knowledge.relation publish failed event=%s: %s", event_type, exc)
