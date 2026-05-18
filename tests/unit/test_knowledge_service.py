# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge lifecycle invariants.

Covers create -> update -> supersede -> retire transitions and the
canonical guarantees: every update produces a new version row, the
item pointer flips to the new version, and retired items cannot be
updated.
"""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.knowledge import (
    KnowledgeItemAlreadyRetired,
    KnowledgeService,
)
from flycanon.interfaces.dtos.knowledge import (
    CreateKnowledgeRequest,
    RetireKnowledgeRequest,
    SupersedeKnowledgeRequest,
    UpdateKnowledgeRequest,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


@pytest.fixture
def settings() -> CanonSettings:
    return CanonSettings()


@pytest.fixture
def audit(repositories, settings):
    return AuditService(
        repository=repositories["audit"],
        event_publisher=None,
        settings=settings,
    )


@pytest.fixture
def knowledge(repositories, audit, settings):
    return KnowledgeService(
        repository=repositories["knowledge"],
        audit=audit,
        event_publisher=None,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_create_publishes_version_one(knowledge):
    version = await knowledge.create(
        CreateKnowledgeRequest(
            title="First canonical statement",
            body="The body of the canonical statement.",
            domain=Domain.process,
            jurisdiction=Jurisdiction.GLOBAL,
        )
    )
    assert version.version == 1
    assert version.status == KnowledgeStatus.published.value


@pytest.mark.asyncio
async def test_update_appends_new_version_supersedes_previous(knowledge, repositories):
    version = await knowledge.create(
        CreateKnowledgeRequest(
            title="Statement",
            body="initial body",
            domain=Domain.process,
        )
    )
    item_id = version.knowledge_item_id
    updated = await knowledge.update(
        item_id,
        UpdateKnowledgeRequest(body="updated body"),
    )
    assert updated.version == 2
    assert updated.body == "updated body"
    history = await repositories["knowledge"].list_versions(item_id)
    statuses = {row.version: row.status for row in history}
    assert statuses[1] == KnowledgeStatus.superseded.value
    assert statuses[2] == KnowledgeStatus.published.value


@pytest.mark.asyncio
async def test_retire_blocks_further_updates(knowledge):
    version = await knowledge.create(
        CreateKnowledgeRequest(
            title="Statement",
            body="body",
            domain=Domain.process,
        )
    )
    item_id = version.knowledge_item_id
    await knowledge.retire(item_id, RetireKnowledgeRequest(reason="superseded by policy change"))
    with pytest.raises(KnowledgeItemAlreadyRetired):
        await knowledge.update(item_id, UpdateKnowledgeRequest(body="x"))


@pytest.mark.asyncio
async def test_supersede_points_to_target(knowledge):
    a = await knowledge.create(
        CreateKnowledgeRequest(title="A", body="a body", domain=Domain.process)
    )
    b = await knowledge.create(
        CreateKnowledgeRequest(title="B", body="b body", domain=Domain.process)
    )
    superseded = await knowledge.supersede(
        a.knowledge_item_id,
        SupersedeKnowledgeRequest(superseded_by_item_id=b.knowledge_item_id),
    )
    assert superseded.status == KnowledgeStatus.superseded.value
    assert superseded.superseded_by_item_id == b.knowledge_item_id
