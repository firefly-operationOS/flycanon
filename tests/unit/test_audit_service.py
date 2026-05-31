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

"""AuditService persistence + best-effort publish behaviour."""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService


@pytest.mark.asyncio
async def test_record_persists_even_without_publisher(repositories, scope):
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
        **scope,
    )
    assert row.event_type == "source.ingested"
    rows, total = await repositories["audit"].list_events(subject_id="abc")
    assert total == 1
    assert rows[0].payload_json["n_chunks"] == 3


@pytest.mark.asyncio
async def test_publish_failure_does_not_abort_audit(repositories, scope):
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
        **scope,
    )
    # The audit row is still in the table; the broker failure was
    # logged and swallowed.
    rows, total = await repositories["audit"].list_events(subject_id="ki-1")
    assert total == 1
    assert rows[0].id == row.id
