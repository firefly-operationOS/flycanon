# Copyright 2026 Firefly Software Solutions Inc
"""AuditService persistence + best-effort publish behaviour."""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService


@pytest.mark.asyncio
async def test_record_persists_even_without_publisher(repositories):
    audit = AuditService(
        repository=repositories["audit"],
        event_publisher=None,
        settings=CanonSettings(),
    )
    row = await audit.record(
        event_type="source.ingested",
        subject_kind="source",
        subject_id="abc",
        actor="tester",
        payload={"n_chunks": 3},
    )
    assert row.event_type == "source.ingested"
    rows, total = await repositories["audit"].list_events(subject_id="abc")
    assert total == 1
    assert rows[0].payload_json["n_chunks"] == 3


@pytest.mark.asyncio
async def test_publish_failure_does_not_abort_audit(repositories):
    class FailingPublisher:
        async def publish(self, **_):
            raise RuntimeError("broker down")

    audit = AuditService(
        repository=repositories["audit"],
        event_publisher=FailingPublisher(),
        settings=CanonSettings(),
    )
    row = await audit.record(
        event_type="knowledge.published",
        subject_kind="knowledge_item",
        subject_id="ki-1",
        actor=None,
        payload={},
    )
    # The audit row is still in the table; the broker failure was
    # logged and swallowed.
    rows, total = await repositories["audit"].list_events(subject_id="ki-1")
    assert total == 1
    assert rows[0].id == row.id
