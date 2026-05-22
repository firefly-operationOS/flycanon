# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end coverage for the ``/api/v1/workspaces`` controller.

Exercises the controller methods directly with a stubbed Starlette
:class:`Request` so the full handler chain (TenantContext extraction
-> repository call -> DTO conversion) is verified without spinning up
the pyfly DI graph. Mirrors how the other controller-flavoured unit
tests interact with their services.

Coverage matches the Plan 4 contract:

* ``POST``           -> 201 + :class:`WorkspaceSpec`.
* ``GET ""``         -> list of :class:`WorkspaceSummary` filtered by tenant.
* ``GET /{id}``      -> :class:`WorkspaceSpec` or 404 ``workspace_not_found``.
* ``PATCH /{id}``    -> sparse update returns the refreshed :class:`WorkspaceSpec`.
* ``POST /{id}:close`` -> transitions ``status -> closed`` + sets ``closed_at``.
* Duplicate id ``POST`` raises ``IntegrityError`` (caught upstream as a 409).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from flycanon.interfaces.dtos.workspace import (
    WorkspaceCreate,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
)
from flycanon.interfaces.enums.workspace_status import WorkspaceStatus
from flycanon.models.repositories.workspace_repository import WorkspaceRepository
from flycanon.web.controllers.workspaces_controller import WorkspacesController
from flycanon.web.conventions import WorkspaceNotFound


@pytest.fixture
def workspace_repo(engine, session_factory) -> WorkspaceRepository:
    return WorkspaceRepository(session_factory, engine=engine)


@pytest.fixture
def controller(workspace_repo: WorkspaceRepository) -> WorkspacesController:
    return WorkspacesController(workspace_repo)


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
