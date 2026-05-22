# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the knowledge-relation surface.

Tests the service directly (no DI / no HTTP) so the wiring is
verified against the real SQLite test repository.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flycanon.core.services.audit import AuditService
from flycanon.core.services.knowledge.errors import KnowledgeItemNotFound
from flycanon.core.services.knowledge.relation_service import (
    InvalidRelationError,
    KnowledgeRelationService,
    RelationConflictError,
    RelationNotFoundError,
)
from flycanon.interfaces.dtos.relation import CreateRelationRequest
from flycanon.interfaces.enums import RelationKind
from flycanon.models.entities.knowledge_item import KnowledgeItemRow


@pytest.fixture
def settings():
    return SimpleNamespace(
        knowledge_topic="flycanon.knowledge",
        audit_topic="flycanon.audit",
        audit_event="AuditEventRecorded",
    )


@pytest.fixture
def relation_service(repositories, settings):
    audit = AuditService(
        repository=repositories["audit"],
        event_publisher=None,
        settings=settings,
    )
    return KnowledgeRelationService(
        knowledge_repository=repositories["knowledge"],
        relation_repository=repositories["relation"],
        audit=audit,
        event_publisher=None,
        settings=settings,
    )


async def _seed_item(repo, item_id: str, title: str = "t") -> None:
    await repo.upsert_item(
        KnowledgeItemRow(
            id=item_id,
            tenant_id="default",
            workspace_id="default",
            status="published",
            current_version=1,
            title=title,
            domain="process",
            jurisdiction="GLOBAL",
            tags_json=[],
        )
    )


class TestAddRelation:
    @pytest.mark.asyncio
    async def test_directed_link_persists(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "a")
        await _seed_item(repositories["knowledge"], "b")
        row = await relation_service.add(
            "a",
            CreateRelationRequest(to_item_id="b", kind=RelationKind.depends_on),
            **scope,
        )
        assert row.from_item_id == "a"
        assert row.to_item_id == "b"
        assert row.kind == "depends_on"

    @pytest.mark.asyncio
    async def test_self_relation_rejected(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "a")
        with pytest.raises(InvalidRelationError):
            await relation_service.add(
                "a",
                CreateRelationRequest(to_item_id="a", kind=RelationKind.related),
                **scope,
            )

    @pytest.mark.asyncio
    async def test_unknown_from_item_raises(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "b")
        with pytest.raises(KnowledgeItemNotFound):
            await relation_service.add(
                "missing",
                CreateRelationRequest(to_item_id="b", kind=RelationKind.related),
                **scope,
            )

    @pytest.mark.asyncio
    async def test_unknown_to_item_raises(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "a")
        with pytest.raises(InvalidRelationError):
            await relation_service.add(
                "a",
                CreateRelationRequest(to_item_id="ghost", kind=RelationKind.related),
                **scope,
            )

    @pytest.mark.asyncio
    async def test_duplicate_kind_pair_rejected(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "a")
        await _seed_item(repositories["knowledge"], "b")
        await relation_service.add(
            "a", CreateRelationRequest(to_item_id="b", kind=RelationKind.related), **scope
        )
        with pytest.raises(RelationConflictError):
            await relation_service.add(
                "a",
                CreateRelationRequest(to_item_id="b", kind=RelationKind.related),
                **scope,
            )

    @pytest.mark.asyncio
    async def test_different_kind_same_pair_allowed(self, repositories, relation_service, scope):
        # ``a -[related]-> b`` and ``a -[depends_on]-> b`` co-exist
        # (different semantics; ((from, to, kind)) uniqueness only).
        await _seed_item(repositories["knowledge"], "a")
        await _seed_item(repositories["knowledge"], "b")
        await relation_service.add(
            "a", CreateRelationRequest(to_item_id="b", kind=RelationKind.related), **scope
        )
        await relation_service.add(
            "a", CreateRelationRequest(to_item_id="b", kind=RelationKind.depends_on), **scope
        )
        out, _ = await relation_service.list_for_item("a", **scope)
        kinds = {row.kind for row in out}
        assert kinds == {"related", "depends_on"}


class TestListForItem:
    @pytest.mark.asyncio
    async def test_outgoing_and_incoming_split(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "a")
        await _seed_item(repositories["knowledge"], "b")
        await _seed_item(repositories["knowledge"], "c")
        await relation_service.add(
            "a", CreateRelationRequest(to_item_id="b", kind=RelationKind.related), **scope
        )
        await relation_service.add(
            "c", CreateRelationRequest(to_item_id="a", kind=RelationKind.depends_on), **scope
        )
        out, inc = await relation_service.list_for_item("a", **scope)
        assert [r.to_item_id for r in out] == ["b"]
        assert [r.from_item_id for r in inc] == ["c"]


class TestRemoveRelation:
    @pytest.mark.asyncio
    async def test_remove_deletes_row(self, repositories, relation_service, scope):
        await _seed_item(repositories["knowledge"], "a")
        await _seed_item(repositories["knowledge"], "b")
        row = await relation_service.add(
            "a", CreateRelationRequest(to_item_id="b", kind=RelationKind.related), **scope
        )
        await relation_service.remove(row.id, **scope)
        out, _ = await relation_service.list_for_item("a", **scope)
        assert out == []

    @pytest.mark.asyncio
    async def test_remove_unknown_id_raises(self, relation_service, scope):
        with pytest.raises(RelationNotFoundError):
            await relation_service.remove("does-not-exist", **scope)
