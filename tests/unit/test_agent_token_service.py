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

"""AgentTokenService: mint, verify, list, revoke."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from flycanon.core.services.auth import agent_token_service as ats_module
from flycanon.core.services.auth.agent_token_service import (
    AgentScopeDenied,
    AgentTokenExpired,
    AgentTokenService,
    AgentWorkspaceNotInAllowlist,
    InvalidAgentToken,
    MintRequest,
    RateLimitExceeded,
    VerifiedAgentToken,
)


class _InMemoryAgentTokenRepository:
    """Test double for AgentTokenRepository."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def insert(self, row: dict) -> None:
        self._rows[row["id"]] = row

    async def get_by_prefix(self, prefix: str, *, tenant_id: str) -> dict | None:
        for row in self._rows.values():
            if row["prefix"] == prefix and row["tenant_id"] == tenant_id and row["revoked_at"] is None:
                return row
        return None

    async def list_for_tenant(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def revoke(self, token_id: str, *, tenant_id: str, at: datetime) -> bool:
        row = self._rows.get(token_id)
        if not row or row["tenant_id"] != tenant_id or row["revoked_at"] is not None:
            return False
        row["revoked_at"] = at
        return True

    async def mark_used(self, token_id: str, *, tenant_id: str, at: datetime) -> None:
        row = self._rows.get(token_id)
        if row is not None and row["tenant_id"] == tenant_id:
            row["last_used_at"] = at


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
    revoked = await service.revoke(minted.id, tenant_id="acme")
    assert revoked is True

    revoked_again = await service.revoke(minted.id, tenant_id="acme")
    assert revoked_again is False


@pytest.mark.asyncio
async def test_revoke_cross_tenant_is_no_op(service: AgentTokenService) -> None:
    """A caller from tenant B must never be able to revoke tenant A's token.

    Defense-in-depth: the repo's WHERE clause filters by ``(id,
    tenant_id)`` so a cross-tenant ``revoke`` flips zero rows and
    returns False -- even if every other layer (controller pre-check,
    RLS policy) somehow misfired.
    """
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
    revoked = await service.revoke(minted.id, tenant_id="bcorp")
    assert revoked is False
    # The original tenant can still revoke -- the wrong-tenant call
    # above did not silently flip the row.
    revoked_for_real = await service.revoke(minted.id, tenant_id="acme")
    assert revoked_for_real is True


@pytest.mark.asyncio
async def test_verify_treats_cross_tenant_prefix_as_unknown(
    service: AgentTokenService,
) -> None:
    """A token minted for tenant A must look invisible to tenant B's verify.

    The repo's ``get_by_prefix`` filters by ``(prefix, tenant_id)``;
    the service then surfaces this as ``InvalidAgentToken("Unknown
    agent token.")`` -- callers learn nothing about whether the
    prefix belongs to another tenant.
    """
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


@pytest.mark.asyncio
async def test_verify_uses_constant_time_comparison(
    service: AgentTokenService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verify path must compare token hashes via ``secrets.compare_digest``."""
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

    real_compare = ats_module.secrets.compare_digest
    call_count = 0

    def counting_compare(a: object, b: object) -> bool:
        nonlocal call_count
        call_count += 1
        return real_compare(a, b)

    monkeypatch.setattr(ats_module.secrets, "compare_digest", counting_compare)

    await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    assert call_count >= 1, "verify must call secrets.compare_digest on the token hash"


@pytest.mark.asyncio
async def test_mark_used_skipped_when_recently_used(
    repo: _InMemoryAgentTokenRepository,
    service: AgentTokenService,
) -> None:
    """A verify within the 60s dedup window must NOT trigger mark_used."""
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
    # Seed last_used_at to 5 seconds ago.
    repo._rows[minted.id]["last_used_at"] = datetime.now(UTC) - timedelta(seconds=5)

    mark_used_mock = AsyncMock()
    repo.mark_used = mark_used_mock  # type: ignore[method-assign]

    verified = await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    assert verified.token_id == minted.id
    mark_used_mock.assert_not_called()


@pytest.mark.asyncio
async def test_mark_used_called_when_stale(
    repo: _InMemoryAgentTokenRepository,
    service: AgentTokenService,
) -> None:
    """A verify after the 60s dedup window MUST call mark_used."""
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
    # Seed last_used_at to 5 minutes ago -- well outside the 60s window.
    repo._rows[minted.id]["last_used_at"] = datetime.now(UTC) - timedelta(minutes=5)

    mark_used_mock = AsyncMock()
    repo.mark_used = mark_used_mock  # type: ignore[method-assign]

    await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    mark_used_mock.assert_called_once()


@pytest.mark.asyncio
async def test_mark_used_called_when_never_used(
    repo: _InMemoryAgentTokenRepository,
    service: AgentTokenService,
) -> None:
    """A first-ever verify (last_used_at is None) MUST call mark_used."""
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
    assert repo._rows[minted.id]["last_used_at"] is None

    mark_used_mock = AsyncMock()
    repo.mark_used = mark_used_mock  # type: ignore[method-assign]

    await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    mark_used_mock.assert_called_once()


# -- Rate limit enforcement -----------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_allows_first_n_requests_within_window(
    service: AgentTokenService,
) -> None:
    """The first N verifies within the rolling 60s window must succeed.

    With ``rate_limit_rpm=3`` the bucket admits exactly 3 calls before
    the next one trips. This pins the inclusive lower edge of the
    sliding-window counter (``len < limit_per_minute``).
    """
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=3,
            expires_at=None,
        ),
        actor="user:alice",
    )
    for _ in range(3):
        verified = await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )
        assert verified.token_id == minted.id


@pytest.mark.asyncio
async def test_rate_limit_rejects_request_over_budget(
    service: AgentTokenService,
) -> None:
    """The (N+1)th verify within the window must raise ``RateLimitExceeded`` (429)."""
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=2,
            expires_at=None,
        ),
        actor="user:alice",
    )
    for _ in range(2):
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )
    with pytest.raises(RateLimitExceeded) as excinfo:
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )
    assert excinfo.value.status == 429
    assert excinfo.value.code == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_rate_limit_window_slides_after_60s(
    service: AgentTokenService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the 60s window passes, the bucket refills and admits new requests.

    We patch ``time.monotonic`` inside the service module to fast-
    forward past the window without sleeping; this exercises the
    ``while self._timestamps[0] < cutoff: popleft()`` path on the
    bucket.
    """
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=1,
            expires_at=None,
        ),
        actor="user:alice",
    )
    fake_now = 1000.0

    def _now() -> float:
        return fake_now

    monkeypatch.setattr(ats_module.time, "monotonic", _now)

    # First request consumes the only slot.
    await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    # Second request at the same instant is over budget.
    with pytest.raises(RateLimitExceeded):
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )

    # Fast-forward past the 60s window.
    fake_now = 1061.0
    # Now the bucket has slid and admits a fresh request.
    verified = await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    assert verified.token_id == minted.id


@pytest.mark.asyncio
async def test_rate_limit_none_means_unlimited(
    service: AgentTokenService,
) -> None:
    """``rate_limit_rpm=None`` MUST bypass the bucket check entirely.

    Pins the contract that legacy tokens (minted before this feature
    landed) keep working with no enforcement. We loop more than any
    reasonable per-minute cap to make the absence of enforcement
    obvious.
    """
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
    for _ in range(50):
        await service.verify(
            minted.token,
            tenant_id="acme",
            workspace_id="ws-x",
            scope="agent.discoveries:validate",
        )


@pytest.mark.asyncio
async def test_revoke_clears_rate_limit_bucket(
    service: AgentTokenService,
) -> None:
    """``revoke`` MUST drop the in-memory bucket for the token.

    Edge case: if the same ``token_id`` is later reused (deterministic
    test fixtures, manual seeding) the new lifecycle should start with
    a fresh window rather than inherit the revoked token's exhausted
    state.
    """
    minted = await service.mint(
        MintRequest(
            tenant_id="acme",
            name="ci-runner",
            workspace_allowlist=None,
            scopes=["agent.discoveries:validate"],
            rate_limit_rpm=2,
            expires_at=None,
        ),
        actor="user:alice",
    )
    # Burn through the budget so the bucket is on file.
    await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    await service.verify(
        minted.token,
        tenant_id="acme",
        workspace_id="ws-x",
        scope="agent.discoveries:validate",
    )
    assert minted.id in service._rate_limiter._buckets  # type: ignore[attr-defined]

    revoked = await service.revoke(minted.id, tenant_id="acme")
    assert revoked is True
    assert minted.id not in service._rate_limiter._buckets  # type: ignore[attr-defined]
