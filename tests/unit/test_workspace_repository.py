# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`WorkspaceRepository`.

These tests run against the in-memory SQLite engine wired in
``tests/conftest.py`` -- mirroring ``test_ingest_job_repository.py``
-- so the schema, fixtures, and async event loop conventions stay
consistent with the rest of the unit suite.
"""

from __future__ import annotations

import pytest

from flycanon.models.repositories.workspace_repository import WorkspaceRepository


@pytest.fixture
def workspace_repo(engine, session_factory) -> WorkspaceRepository:
    return WorkspaceRepository(session_factory, engine=engine)


class TestInsertAndGet:
    @pytest.mark.asyncio
    async def test_insert_then_get_round_trip(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        await workspace_repo.insert(
            {
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Q3 audit",
                "status": "active",
            }
        )
        row = await workspace_repo.get("acme", "ws-1")
        assert row is not None
        assert row["id"] == "ws-1"
        assert row["tenant_id"] == "acme"
        assert row["name"] == "Q3 audit"
        assert row["status"] == "active"
        # Server defaults applied -- timestamps were stamped at insert.
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
        assert row["closed_at"] is None

    @pytest.mark.asyncio
    async def test_get_with_mismatched_tenant_returns_none(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        # Cross-tenant lookup must not leak rows -- the composite
        # ``(tenant_id, id)`` filter is the only thing standing between
        # tenant A and tenant B's workspace metadata.
        await workspace_repo.insert(
            {
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Q3 audit",
                "status": "active",
            }
        )
        assert await workspace_repo.get("bcorp", "ws-1") is None

    @pytest.mark.asyncio
    async def test_get_unknown_workspace_returns_none(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        assert await workspace_repo.get("acme", "ws-missing") is None


class TestListForTenant:
    @pytest.mark.asyncio
    async def test_list_for_tenant_filters_by_tenant(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        await workspace_repo.insert(
            {"id": "ws-1", "tenant_id": "acme", "name": "A", "status": "active"}
        )
        await workspace_repo.insert(
            {"id": "ws-2", "tenant_id": "bcorp", "name": "B", "status": "active"}
        )
        acme = await workspace_repo.list_for_tenant("acme")
        bcorp = await workspace_repo.list_for_tenant("bcorp")
        assert [r["id"] for r in acme] == ["ws-1"]
        assert [r["id"] for r in bcorp] == ["ws-2"]

    @pytest.mark.asyncio
    async def test_list_for_tenant_empty_when_no_rows(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        assert await workspace_repo.list_for_tenant("acme") == []


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_applies_patch_and_bumps_updated_at(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        await workspace_repo.insert(
            {
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Original",
                "status": "active",
            }
        )
        before_row = await workspace_repo.get("acme", "ws-1")
        assert before_row is not None
        before = before_row["updated_at"]

        updated = await workspace_repo.update(
            "acme", "ws-1", {"name": "Renamed"}
        )
        assert updated is not None
        assert updated["name"] == "Renamed"
        # The sparse update path always bumps ``updated_at``; ``>=`` is
        # safe even when the test clock has sub-millisecond resolution.
        assert updated["updated_at"] >= before

    @pytest.mark.asyncio
    async def test_update_drops_none_values_from_patch(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        # A patch that mixes a real value with explicit ``None`` should
        # only write the real value -- the ``None`` must not blank an
        # existing column.
        await workspace_repo.insert(
            {
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Original",
                "status": "active",
                "jurisdiction": "EU",
            }
        )
        updated = await workspace_repo.update(
            "acme",
            "ws-1",
            {"name": "Renamed", "jurisdiction": None},
        )
        assert updated is not None
        assert updated["name"] == "Renamed"
        assert updated["jurisdiction"] == "EU"

    @pytest.mark.asyncio
    async def test_update_returns_none_for_unknown_workspace(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        assert (
            await workspace_repo.update("acme", "ws-missing", {"name": "x"})
            is None
        )


class TestClose:
    @pytest.mark.asyncio
    async def test_close_sets_status_and_closed_at(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        await workspace_repo.insert(
            {
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Q3",
                "status": "active",
            }
        )
        closed = await workspace_repo.close("acme", "ws-1")
        assert closed is True
        row = await workspace_repo.get("acme", "ws-1")
        assert row is not None
        assert row["status"] == "closed"
        assert row["closed_at"] is not None

    @pytest.mark.asyncio
    async def test_close_unknown_workspace_returns_false(
        self, workspace_repo: WorkspaceRepository
    ) -> None:
        assert await workspace_repo.close("acme", "ws-missing") is False
