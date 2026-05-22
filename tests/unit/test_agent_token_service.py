# Copyright 2026 Firefly Software Solutions Inc
"""AgentTokenService: mint, verify, list, revoke."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flycanon.core.services.auth.agent_token_service import (
    AgentScopeDenied,
    AgentTokenExpired,
    AgentTokenService,
    AgentWorkspaceNotInAllowlist,
    InvalidAgentToken,
    MintRequest,
    VerifiedAgentToken,
)


class _InMemoryAgentTokenRepository:
    """Test double for AgentTokenRepository."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def insert(self, row: dict) -> None:
        self._rows[row["id"]] = row

    async def get_by_prefix(self, prefix: str) -> dict | None:
        for row in self._rows.values():
            if row["prefix"] == prefix and row["revoked_at"] is None:
                return row
        return None

    async def list_for_tenant(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def revoke(self, token_id: str, *, at: datetime) -> bool:
        row = self._rows.get(token_id)
        if not row or row["revoked_at"] is not None:
            return False
        row["revoked_at"] = at
        return True

    async def mark_used(self, token_id: str, *, at: datetime) -> None:
        if token_id in self._rows:
            self._rows[token_id]["last_used_at"] = at


@pytest.fixture
def repo() -> _InMemoryAgentTokenRepository:
    return _InMemoryAgentTokenRepository()


@pytest.fixture
def service(repo: _InMemoryAgentTokenRepository) -> AgentTokenService:
    return AgentTokenService(repo)


@pytest.mark.asyncio
async def test_mint_returns_full_secret_once(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    assert minted.token.startswith("agt_")
    assert minted.prefix == minted.token[:12]
    assert len(minted.token) > 30  # secret part is meaningful


@pytest.mark.asyncio
async def test_verify_returns_token_when_valid(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    verified = await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    assert isinstance(verified, VerifiedAgentToken)
    assert verified.token_id == minted.id
    assert verified.actor == f"agent:{minted.prefix}"


@pytest.mark.asyncio
async def test_verify_rejects_unknown_token(service: AgentTokenService) -> None:
    with pytest.raises(InvalidAgentToken):
        await service.verify(
            "agt_00000000_unknown",
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )


@pytest.mark.asyncio
async def test_verify_rejects_wrong_tenant(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    with pytest.raises(InvalidAgentToken):
        await service.verify(
            minted.token,
            tenant_id="bcorp",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )


@pytest.mark.asyncio
async def test_verify_rejects_expired_token(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ),
        actor="user:alice",
    )
    with pytest.raises(AgentTokenExpired):
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )


@pytest.mark.asyncio
async def test_verify_rejects_workspace_not_in_allowlist(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=["ws-prod"],
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    with pytest.raises(AgentWorkspaceNotInAllowlist):
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-dev",
            scope="agent.discoveries:validate",
        )


@pytest.mark.asyncio
async def test_verify_rejects_scope_denied(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    with pytest.raises(AgentScopeDenied):
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discovery-jobs:submit",
        )


@pytest.mark.asyncio
async def test_revoke_marks_token_revoked(service: AgentTokenService) -> None:
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["*"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    revoked = await service.revoke(minted.id)
    assert revoked is True

    revoked_again = await service.revoke(minted.id)
    assert revoked_again is False


@pytest.mark.asyncio
async def test_list_omits_secret(service: AgentTokenService) -> None:
    await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["*"],
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="user:alice",
    )
    summaries = await service.list_for_tenant("acme")
    assert len(summaries) == 1
    s = summaries[0]
    assert hasattr(s, "prefix")
    assert not hasattr(s, "secret_hash")
    assert not hasattr(s, "token")
