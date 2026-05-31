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

"""Unit coverage for :class:`WorkspaceEventPublisher`.

Verifies the publisher:

* Translates a workspace-row dict into the matching event DTO.
* Stamps a timezone-aware UTC ``occurred_at``.
* Emits the right ``event_type`` literal for each lifecycle method.
* Pushes to the pyfly :class:`EventPublisher` and to every attached
  in-process listener.
* Swallows EDA-bus failures so consumers can replay from the
  durable store (the workspace row + audit log) -- the listener
  fan-out still runs even when the bus blows up.

The pyfly :class:`EventPublisher` is stubbed with a minimal class
that captures every ``publish(...)`` call -- we don't need the full
EDA adapter for unit verification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.events import WorkspaceEventPublisher
from flycanon.interfaces.dtos.workspace_event import (
    WorkspaceCreated,
    WorkspaceDeleted,
    WorkspaceEventBase,
    WorkspaceUpdated,
)


class _CapturingPublisher:
    """Minimal stub for :class:`pyfly.eda.EventPublisher`.

    Records every ``publish(...)`` call so the test can inspect the
    destination + event_type + payload combination handed to pyfly's
    bus.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def publish(
        self,
        *,
        destination: str,
        event_type: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.calls.append(
            {
                "destination": destination,
                "event_type": event_type,
                "payload": payload,
                "headers": headers,
            }
        )

    def subscribe(self, *_: Any, **__: Any) -> None:  # pragma: no cover - protocol
        return None

    async def start(self) -> None:  # pragma: no cover - protocol
        return None

    async def stop(self) -> None:  # pragma: no cover - protocol
        return None


class _FailingPublisher(_CapturingPublisher):
    """Raises on every publish -- exercises the swallow-and-log branch."""

    async def publish(self, **_: Any) -> None:
        raise RuntimeError("broker down")


def _workspace_row(
    *,
    workspace_id: str = "ws-1",
    tenant_id: str = "acme",
    name: str = "Q3 audit",
    scope: list[Any] | None = None,
    sme_roster: list[Any] | None = None,
    retention_days: int | None = None,
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    """Build a row dict shaped like ``WorkspaceRepository.get`` returns.

    Storage-side keys (``scope_json``, ``sme_roster_json``) mirror
    what the repository hands the controller -- the publisher must
    translate them into the wire field names on the event.
    """
    return {
        "id": workspace_id,
        "tenant_id": tenant_id,
        "name": name,
        "status": "active",
        "scope_json": scope,
        "sme_roster_json": sme_roster,
        "retention_days": retention_days,
        "jurisdiction": jurisdiction,
        "created_at": datetime.now(),  # noqa: DTZ005 -- harness, value not asserted
        "updated_at": datetime.now(),  # noqa: DTZ005
        "closed_at": None,
    }


@pytest.fixture
def settings() -> CanonSettings:
    return CanonSettings()


@pytest.fixture
def publisher(settings: CanonSettings) -> WorkspaceEventPublisher:
    return WorkspaceEventPublisher(
        event_publisher=_CapturingPublisher(),
        settings=settings,
    )


class TestPublishCreated:
    @pytest.mark.asyncio
    async def test_listener_receives_workspace_created_event(self, settings: CanonSettings) -> None:
        bus = _CapturingPublisher()
        pub = WorkspaceEventPublisher(event_publisher=bus, settings=settings)
        captured: list[WorkspaceEventBase] = []

        async def listener(event: WorkspaceEventBase) -> None:
            captured.append(event)

        pub.add_listener(listener)
        row = _workspace_row(
            workspace_id="ws-1",
            tenant_id="acme",
            name="Q3 audit",
            scope=[{"department": "audit"}],
            sme_roster=[{"user": "alice"}],
            retention_days=90,
            jurisdiction="EU",
        )

        await pub.publish_created(row)

        assert len(captured) == 1
        event = captured[0]
        assert isinstance(event, WorkspaceCreated)
        assert event.event_type == "workspace.created"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.name == "Q3 audit"
        assert event.scope == [{"department": "audit"}]
        assert event.sme_roster == [{"user": "alice"}]
        assert event.retention_days == 90
        assert event.jurisdiction == "EU"

    @pytest.mark.asyncio
    async def test_occurred_at_is_timezone_aware_utc(self, publisher: WorkspaceEventPublisher) -> None:
        captured: list[WorkspaceEventBase] = []

        async def listener(event: WorkspaceEventBase) -> None:
            captured.append(event)

        publisher.add_listener(listener)
        await publisher.publish_created(_workspace_row())

        event = captured[0]
        assert event.occurred_at.tzinfo is not None
        assert event.occurred_at.utcoffset() is not None
        assert event.occurred_at.utcoffset().total_seconds() == 0

    @pytest.mark.asyncio
    async def test_event_pushed_to_pyfly_bus(self, settings: CanonSettings) -> None:
        bus = _CapturingPublisher()
        pub = WorkspaceEventPublisher(event_publisher=bus, settings=settings)
        await pub.publish_created(_workspace_row())
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert call["destination"] == "canon.workspaces.v1"
        assert call["event_type"] == "WorkspaceCreated"
        # Payload is the JSON-serialised event DTO.
        assert call["payload"]["event_type"] == "workspace.created"
        assert call["payload"]["tenant_id"] == "acme"
        assert call["payload"]["workspace_id"] == "ws-1"


class TestPublishUpdated:
    @pytest.mark.asyncio
    async def test_listener_receives_workspace_updated_event(
        self, publisher: WorkspaceEventPublisher
    ) -> None:
        captured: list[WorkspaceEventBase] = []

        async def listener(event: WorkspaceEventBase) -> None:
            captured.append(event)

        publisher.add_listener(listener)
        await publisher.publish_updated(
            _workspace_row(
                workspace_id="ws-1",
                tenant_id="acme",
                name="Renamed",
                retention_days=365,
            )
        )

        assert len(captured) == 1
        event = captured[0]
        assert isinstance(event, WorkspaceUpdated)
        assert event.event_type == "workspace.updated"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.name == "Renamed"
        assert event.retention_days == 365

    @pytest.mark.asyncio
    async def test_event_pushed_to_pyfly_bus(self, settings: CanonSettings) -> None:
        bus = _CapturingPublisher()
        pub = WorkspaceEventPublisher(event_publisher=bus, settings=settings)
        await pub.publish_updated(_workspace_row(workspace_id="ws-2", tenant_id="bcorp"))
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert call["destination"] == "canon.workspaces.v1"
        assert call["event_type"] == "WorkspaceUpdated"
        assert call["payload"]["event_type"] == "workspace.updated"


class TestPublishDeleted:
    @pytest.mark.asyncio
    async def test_listener_receives_workspace_deleted_event(
        self, publisher: WorkspaceEventPublisher
    ) -> None:
        captured: list[WorkspaceEventBase] = []

        async def listener(event: WorkspaceEventBase) -> None:
            captured.append(event)

        publisher.add_listener(listener)
        await publisher.publish_deleted(tenant_id="acme", workspace_id="ws-1")

        assert len(captured) == 1
        event = captured[0]
        assert isinstance(event, WorkspaceDeleted)
        assert event.event_type == "workspace.deleted"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.occurred_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_event_pushed_to_pyfly_bus(self, settings: CanonSettings) -> None:
        bus = _CapturingPublisher()
        pub = WorkspaceEventPublisher(event_publisher=bus, settings=settings)
        await pub.publish_deleted(tenant_id="acme", workspace_id="ws-1")
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert call["destination"] == "canon.workspaces.v1"
        assert call["event_type"] == "WorkspaceDeleted"
        assert call["payload"]["event_type"] == "workspace.deleted"


class TestBackendResilience:
    @pytest.mark.asyncio
    async def test_eda_failure_is_swallowed_listener_still_fires(self, settings: CanonSettings) -> None:
        """Bus blowing up doesn't abort the publish call.

        Best-effort consistency: the durable workspace row is the
        truth; the bus is a notification side-channel. The in-process
        listener still receives the event so the rest of the system
        (and our tests) keep working.
        """
        bus = _FailingPublisher()
        pub = WorkspaceEventPublisher(event_publisher=bus, settings=settings)
        captured: list[WorkspaceEventBase] = []

        async def listener(event: WorkspaceEventBase) -> None:
            captured.append(event)

        pub.add_listener(listener)
        # No exception expected -- the bus failure is logged + swallowed.
        await pub.publish_created(_workspace_row())
        assert len(captured) == 1
        assert isinstance(captured[0], WorkspaceCreated)


class TestListenerLifecycle:
    @pytest.mark.asyncio
    async def test_clear_listeners_drops_attached(self, publisher: WorkspaceEventPublisher) -> None:
        captured: list[WorkspaceEventBase] = []

        async def listener(event: WorkspaceEventBase) -> None:
            captured.append(event)

        publisher.add_listener(listener)
        publisher.clear_listeners()
        await publisher.publish_created(_workspace_row())
        assert captured == []

    @pytest.mark.asyncio
    async def test_multiple_listeners_each_invoked(self, publisher: WorkspaceEventPublisher) -> None:
        a: list[WorkspaceEventBase] = []
        b: list[WorkspaceEventBase] = []

        async def la(event: WorkspaceEventBase) -> None:
            a.append(event)

        async def lb(event: WorkspaceEventBase) -> None:
            b.append(event)

        publisher.add_listener(la)
        publisher.add_listener(lb)
        await publisher.publish_created(_workspace_row())
        assert len(a) == 1
        assert len(b) == 1
