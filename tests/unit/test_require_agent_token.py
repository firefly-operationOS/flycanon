# Copyright 2026 Firefly Software Solutions Inc
"""``require_agent_token`` -- per-call dep that verifies the token."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from flycanon.core.services.auth.agent_token_service import (
    AgentTokenService,
    MintRequest,
)
from flycanon.web.agent_deps import require_agent_token
from flycanon.web.conventions import (
    TenantContext,
    register_exception_handlers,
)


class _InMemoryAgentTokenRepository:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def insert(self, row: dict) -> None:
        self._rows[row["id"]] = row

    async def get_by_prefix(self, prefix: str, *, tenant_id: str) -> dict | None:
        for row in self._rows.values():
            if (
                row["prefix"] == prefix
                and row["tenant_id"] == tenant_id
                and row["revoked_at"] is None
            ):
                return row
        return None

    async def list_for_tenant(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def revoke(self, token_id: str, *, tenant_id: str, at: datetime) -> bool:
        return False

    async def mark_used(self, token_id: str, *, tenant_id: str, at: datetime) -> None:
        return None


def _app_with_dep(service: AgentTokenService) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    def _service_factory() -> AgentTokenService:
        return service

    _dep = require_agent_token(
        scope="agent.discoveries:validate",
        service=_service_factory,
    )

    @app.get("/who")
    async def _who(
        ctx: TenantContext = Depends(_dep),  # noqa: B008
    ) -> dict:
        return {
            "tenant": ctx.tenant_id,
            "workspace": ctx.workspace_id,
            "actor": ctx.actor,
        }

    return app


@pytest.mark.asyncio
async def test_happy_path_returns_context_with_agent_actor() -> None:
    repo = _InMemoryAgentTokenRepository()
    service = AgentTokenService(repo)
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )

    app = _app_with_dep(service)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Agent-Token": minted.token,
        },
    )
    assert response.status_code == 200
    assert response.json()["actor"] == f"agent:{minted.prefix}"


def test_missing_token_returns_401() -> None:
    repo = _InMemoryAgentTokenRepository()
    service = AgentTokenService(repo)
    app = _app_with_dep(service)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/who",
        headers={"X-Tenant-Id": "acme", "X-Workspace-Id": "ws-q3"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "missing_agent_token"


def test_unknown_token_returns_403() -> None:
    repo = _InMemoryAgentTokenRepository()
    service = AgentTokenService(repo)
    app = _app_with_dep(service)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Agent-Token": "agt_deadbeef_unknownsecret",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "invalid_agent_token"
