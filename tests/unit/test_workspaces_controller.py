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

"""End-to-end coverage for the ``/api/v1/workspaces`` controller.

Exercises the controller methods directly with a stubbed Starlette
:class:`Request` so the full handler chain (TenantContext extraction
-> repository call -> DTO conversion) is verified without spinning up
the pyfly DI graph. Mirrors how the other controller-flavoured unit
tests interact with their services.

Coverage matches the workspace CRUD contract:

* ``POST``           -> 201 + :class:`WorkspaceSpec`.
* ``GET ""``         -> list of :class:`WorkspaceSummary` filtered by tenant.
* ``GET /{id}``      -> :class:`WorkspaceSpec` or 404 ``workspace_not_found``.
* ``PATCH /{id}``    -> sparse update returns the refreshed :class:`WorkspaceSpec`.
* ``POST /{id}:close`` -> transitions ``status -> closed`` + sets ``closed_at``.
* Duplicate id ``POST`` raises ``IntegrityError`` (caught upstream as a 409).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from flycanon.config import CanonSettings
from flycanon.core.services.events import WorkspaceEventPublisher
from flycanon.interfaces.dtos.workspace import (
    WorkspaceCreate,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
)
from flycanon.interfaces.dtos.workspace_event import (
    WorkspaceCreated,
    WorkspaceDeleted,
    WorkspaceEventBase,
    WorkspaceUpdated,
)
from flycanon.interfaces.enums.workspace_status import WorkspaceStatus
from flycanon.models.repositories.workspace_repository import WorkspaceRepository
from flycanon.web.controllers.workspaces_controller import WorkspacesController
from flycanon.web.conventions import WorkspaceNotFound


class _NullPublisher:
    """No-op pyfly :class:`EventPublisher` stub for unit tests.

    The controller's lifecycle paths each call into
    :class:`WorkspaceEventPublisher`, which in production forwards to
    pyfly's bus. We don't exercise the bus in these tests -- the
    in-process listener fan-out (see ``_recording_publisher``) is
    enough to assert what each route emits.
    """

    async def publish(self, **_: Any) -> None:
        return None

    def subscribe(self, *_: Any, **__: Any) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.fixture
def workspace_repo(engine, session_factory) -> WorkspaceRepository:
    return WorkspaceRepository(session_factory, engine=engine)


@pytest.fixture
def captured_events() -> list[WorkspaceEventBase]:
    return []


@pytest.fixture
def event_publisher(
    captured_events: list[WorkspaceEventBase],
) -> WorkspaceEventPublisher:
    pub = WorkspaceEventPublisher(
        event_publisher=_NullPublisher(),
        settings=CanonSettings(),
    )

    async def _listener(event: WorkspaceEventBase) -> None:
        captured_events.append(event)

    pub.add_listener(_listener)
    return pub


@pytest.fixture
def controller(
    workspace_repo: WorkspaceRepository,
    event_publisher: WorkspaceEventPublisher,
) -> WorkspacesController:
    return WorkspacesController(workspace_repo, event_publisher)


def _request(tenant_id: str = "acme", workspace_id: str = "default") -> object:
    """Build a Starlette-compatible request stub.

    ``tenant_context_from_request`` reads ``request.headers.get(...)``;
    a SimpleNamespace with a ``headers`` dict-like is enough to feed
    the convention helper without a full Starlette Request.
    """
    headers = {
        "X-Tenant-Id": tenant_id,
        "X-Workspace-Id": workspace_id,
    }
    return SimpleNamespace(headers=headers)


class TestCreate:
    @pytest.mark.asyncio
    async def test_post_creates_and_returns_spec(self, controller: WorkspacesController) -> None:
        body = WorkspaceCreate(
            id="ws-1",
            name="Q3 audit",
            status=WorkspaceStatus.active,
        )
        spec = await controller.create(_request(tenant_id="acme"), body)
        assert isinstance(spec, WorkspaceSpec)
        assert spec.id == "ws-1"
        assert spec.tenant_id == "acme"
        assert spec.name == "Q3 audit"
        assert spec.status == WorkspaceStatus.active
        assert spec.created_at is not None
        assert spec.updated_at is not None
        assert spec.closed_at is None

    @pytest.mark.asyncio
    async def test_duplicate_id_raises_integrity_error(self, controller: WorkspacesController) -> None:
        body = WorkspaceCreate(id="ws-dup", name="A", status=WorkspaceStatus.active)
        await controller.create(_request(tenant_id="acme"), body)
        with pytest.raises(IntegrityError):
            await controller.create(_request(tenant_id="acme"), body)


class TestList:
    @pytest.mark.asyncio
    async def test_list_returns_tenant_workspaces(self, controller: WorkspacesController) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-acme-1", name="A", status=WorkspaceStatus.active),
        )
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-acme-2", name="B", status=WorkspaceStatus.active),
        )
        # Different tenant row -- must NOT surface in acme's list. The
        # workspace id PK is globally unique so the bcorp row uses a
        # distinct id; the partitioning happens at the tenant_id WHERE
        # clause in :meth:`list_for_tenant`, not at id collision.
        await controller.create(
            _request(tenant_id="bcorp"),
            WorkspaceCreate(id="ws-bcorp-1", name="cross-tenant", status=WorkspaceStatus.active),
        )
        rows = await controller.list_workspaces(_request(tenant_id="acme"))
        assert all(isinstance(r, WorkspaceSummary) for r in rows)
        ids = {r.id for r in rows}
        # Only the two acme rows surface; the bcorp row is filtered out.
        assert ids == {"ws-acme-1", "ws-acme-2"}
        assert {r.tenant_id for r in rows} == {"acme"}


class TestGet:
    @pytest.mark.asyncio
    async def test_get_returns_spec(self, controller: WorkspacesController) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        spec = await controller.get(_request(tenant_id="acme"), "ws-1")
        assert isinstance(spec, WorkspaceSpec)
        assert spec.id == "ws-1"
        assert spec.tenant_id == "acme"

    @pytest.mark.asyncio
    async def test_get_unknown_raises_404(self, controller: WorkspacesController) -> None:
        with pytest.raises(WorkspaceNotFound):
            await controller.get(_request(tenant_id="acme"), "ws-missing")

    @pytest.mark.asyncio
    async def test_get_cross_tenant_raises_404(self, controller: WorkspacesController) -> None:
        # Workspace lives under tenant=acme; bcorp must not see it.
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        with pytest.raises(WorkspaceNotFound):
            await controller.get(_request(tenant_id="bcorp"), "ws-1")


class TestUpdate:
    @pytest.mark.asyncio
    async def test_patch_applies_sparse_update(self, controller: WorkspacesController) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="Original", status=WorkspaceStatus.active),
        )
        updated = await controller.update(
            _request(tenant_id="acme"),
            "ws-1",
            WorkspaceUpdate(name="Renamed"),
        )
        assert isinstance(updated, WorkspaceSpec)
        assert updated.name == "Renamed"
        assert updated.status == WorkspaceStatus.active

    @pytest.mark.asyncio
    async def test_patch_unknown_raises_404(self, controller: WorkspacesController) -> None:
        with pytest.raises(WorkspaceNotFound):
            await controller.update(
                _request(tenant_id="acme"),
                "ws-missing",
                WorkspaceUpdate(name="x"),
            )


class TestClose:
    @pytest.mark.asyncio
    async def test_close_transitions_status_and_stamps_closed_at(
        self, controller: WorkspacesController
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        closed = await controller.close(_request(tenant_id="acme"), "ws-1")
        assert isinstance(closed, WorkspaceSpec)
        assert closed.status == WorkspaceStatus.closed
        assert closed.closed_at is not None

    @pytest.mark.asyncio
    async def test_close_unknown_raises_404(self, controller: WorkspacesController) -> None:
        with pytest.raises(WorkspaceNotFound):
            await controller.close(_request(tenant_id="acme"), "ws-missing")


class TestLifecycleEvents:
    """Each mutation route emits the matching workspace lifecycle event.

    The fixture-attached listener captures every event the publisher
    fans out -- production code goes through the same code path,
    additionally pushing onto pyfly's bus (stubbed out as a no-op
    here).
    """

    @pytest.mark.asyncio
    async def test_post_emits_workspace_created(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="Q3 audit", status=WorkspaceStatus.active),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert isinstance(event, WorkspaceCreated)
        assert event.event_type == "workspace.created"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.name == "Q3 audit"
        assert event.occurred_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_patch_emits_workspace_updated(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="Original", status=WorkspaceStatus.active),
        )
        captured_events.clear()  # drop the WorkspaceCreated from create()
        await controller.update(
            _request(tenant_id="acme"),
            "ws-1",
            WorkspaceUpdate(name="Renamed"),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert isinstance(event, WorkspaceUpdated)
        assert event.event_type == "workspace.updated"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.name == "Renamed"

    @pytest.mark.asyncio
    async def test_close_emits_workspace_deleted(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        captured_events.clear()
        await controller.close(_request(tenant_id="acme"), "ws-1")
        assert len(captured_events) == 1
        event = captured_events[0]
        assert isinstance(event, WorkspaceDeleted)
        assert event.event_type == "workspace.deleted"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"

    @pytest.mark.asyncio
    async def test_failed_create_publishes_no_event(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """Best-effort consistency: event only fires after a successful repo write.

        Duplicate-id ``POST`` raises ``IntegrityError`` *before* the
        controller reaches the publish call -- the listener must
        not see a phantom WorkspaceCreated.
        """
        body = WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active)
        await controller.create(_request(tenant_id="acme"), body)
        captured_events.clear()
        with pytest.raises(IntegrityError):
            await controller.create(_request(tenant_id="acme"), body)
        assert captured_events == []

    @pytest.mark.asyncio
    async def test_failed_update_publishes_no_event(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """A PATCH against a missing workspace must not publish."""
        with pytest.raises(WorkspaceNotFound):
            await controller.update(
                _request(tenant_id="acme"),
                "ws-missing",
                WorkspaceUpdate(name="x"),
            )
        assert captured_events == []

    @pytest.mark.asyncio
    async def test_failed_close_publishes_no_event(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """A ``:close`` against a missing workspace must not publish."""
        with pytest.raises(WorkspaceNotFound):
            await controller.close(_request(tenant_id="acme"), "ws-missing")
        assert captured_events == []
