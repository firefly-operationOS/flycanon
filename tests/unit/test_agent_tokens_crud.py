# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end coverage for the ``/api/v1/agent-tokens`` controller.

Exercises the controller methods directly with a stubbed Starlette
:class:`Request` so the full handler chain (TenantContext extraction
-> service call -> DTO conversion) is verified without spinning up
the pyfly DI graph. Mirrors how the other controller-flavoured unit
tests interact with their services -- see
``test_workspaces_controller.py`` for the canonical shape.

Coverage matches the Plan 5 Task 4 contract:

* ``POST``          -> 201 + :class:`AgentTokenCreated` with the
  full ``token`` populated exactly once.
* ``GET ""``        -> list of :class:`AgentTokenSummaryDto`
  filtered by tenant; secret hash and raw token both omitted.
* ``DELETE /{id}``  -> 204 No Content; idempotent re-revoke; 404
  ``resource_not_found`` on unknown id.
* Agent-tier caller (``X-Agent-Token`` -> ``ctx.actor`` prefixed
  ``"agent:"``) raises ``AgentCannotMint`` on mint.
* Re-minting the same ``name`` succeeds and returns a fresh secret
  -- the human label is intentionally not unique.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flycanon.core.services.auth.agent_token_service import (
    AgentCannotMint,
    AgentTokenService,
)
from flycanon.interfaces.dtos.agent_token import (
    AgentTokenCreated,
    AgentTokenMintRequest,
    AgentTokenSummaryDto,
)
from flycanon.web.controllers.agent_tokens_controller import AgentTokensController
from flycanon.web.conventions import ResourceNotFound


class _InMemoryAgentTokenRepository:
    """Test double for :class:`AgentTokenRepository`.

    Mirrors the production repository's contract while keeping the
    fixture set tight: only the methods the service hits are
    implemented. Rows are returned in insertion order; the
    production repository orders by ``created_at DESC`` but the unit
    tests don't assert on ordering so insertion order is acceptable
    here.
    """

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


class _StubRequest:
    """Starlette-compatible request stub.

    ``tenant_context_from_request`` reads ``request.headers.get(...)``;
    a thin class with a dict-like ``headers`` attribute is enough to
    feed the convention helper without a full Starlette Request. We
    avoid ``SimpleNamespace`` so ``pyright --strict`` doesn't choke
    on the ``Request`` type-hint at the controller boundary.
    """

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _request(
    tenant_id: str = "acme",
    workspace_id: str = "ws-it",
    *,
    agent_token: str | None = None,
) -> _StubRequest:
    """Build a stub request carrying the canonical tenant headers.

    When ``agent_token`` is provided the request mimics an agent-tier
    caller -- the convention helper resolves ``ctx.actor`` to
    ``f"agent:{prefix}"`` and the controller refuses mint/list/revoke
    with ``AgentCannotMint``.
    """
    headers: dict[str, str] = {
        "X-Tenant-Id": tenant_id,
        "X-Workspace-Id": workspace_id,
    }
    if agent_token is not None:
        headers["X-Agent-Token"] = agent_token
    return _StubRequest(headers)


@pytest.fixture
def repo() -> _InMemoryAgentTokenRepository:
    return _InMemoryAgentTokenRepository()


@pytest.fixture
def service(repo: _InMemoryAgentTokenRepository) -> AgentTokenService:
    return AgentTokenService(repo)


@pytest.fixture
def controller(service: AgentTokenService) -> AgentTokensController:
    return AgentTokensController(service)


class TestMint:
    @pytest.mark.asyncio
    async def test_mint_returns_full_secret_once(self, controller: AgentTokensController) -> None:
        body = AgentTokenMintRequest(
            name="ci-runner",
            scopes=["agent.discoveries:validate"],
        )
        created = await controller.mint(_request(tenant_id="acme"), body)
        assert isinstance(created, AgentTokenCreated)
        assert created.name == "ci-runner"
        assert created.token.startswith("agt_")
        # The wire prefix is the first 12 chars of the raw token
        # (``agt_<8hex>``) -- both the persisted prefix column and the
        # response field should agree on that.
        assert created.prefix == created.token[:12]
        assert created.scopes == ["agent.discoveries:validate"]
        assert created.revoked_at is None
        assert created.created_by == "anonymous"  # no JWT in this test
        assert created.created_at is not None

    @pytest.mark.asyncio
    async def test_mint_defaults_scopes_to_wildcard(self, controller: AgentTokensController) -> None:
        body = AgentTokenMintRequest(name="default-scopes")
        created = await controller.mint(_request(tenant_id="acme"), body)
        assert created.scopes == ["*"]
        assert created.workspace_allowlist is None
        assert created.rate_limit_rpm is None

    @pytest.mark.asyncio
    async def test_mint_with_workspace_allowlist_persists(self, controller: AgentTokensController) -> None:
        body = AgentTokenMintRequest(
            name="scoped",
            workspace_allowlist=["ws-a", "ws-b"],
            rate_limit_rpm=120,
        )
        created = await controller.mint(_request(tenant_id="acme"), body)
        assert created.workspace_allowlist == ["ws-a", "ws-b"]
        assert created.rate_limit_rpm == 120

    @pytest.mark.asyncio
    async def test_agent_tier_caller_cannot_mint(self, controller: AgentTokensController) -> None:
        # ``actor_from_agent_token`` resolves the actor string to
        # ``f"agent:{prefix}"`` (first 12 chars of the raw token) -- a
        # request carrying ``X-Agent-Token`` mirrors what reaches the
        # controller after the auth middleware ran on a real call.
        body = AgentTokenMintRequest(name="never")
        with pytest.raises(AgentCannotMint):
            await controller.mint(
                _request(tenant_id="acme", agent_token="agt_deadbeef_0123456789abcdef0123456789abcdef"),
                body,
            )


class TestList:
    @pytest.mark.asyncio
    async def test_list_omits_secret(self, controller: AgentTokensController) -> None:
        await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="ci-runner-list"),
        )
        listing = await controller.list_tokens(_request(tenant_id="acme"))
        assert isinstance(listing, list)
        assert all(isinstance(t, AgentTokenSummaryDto) for t in listing)
        # ``AgentTokenSummaryDto`` does not declare a ``token`` field,
        # so pydantic's strict mode keeps the secret off the wire. The
        # assertion below makes the intent explicit -- if the DTO ever
        # gains a ``token`` field this test fails loudly.
        for t in listing:
            assert not hasattr(t, "token")
            assert t.prefix.startswith("agt_")

    @pytest.mark.asyncio
    async def test_list_filters_by_tenant(self, controller: AgentTokensController) -> None:
        await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="acme-token"),
        )
        await controller.mint(
            _request(tenant_id="bcorp"),
            AgentTokenMintRequest(name="bcorp-token"),
        )
        acme = await controller.list_tokens(_request(tenant_id="acme"))
        bcorp = await controller.list_tokens(_request(tenant_id="bcorp"))
        assert {t.name for t in acme} == {"acme-token"}
        assert {t.name for t in bcorp} == {"bcorp-token"}


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_marks_token(self, controller: AgentTokensController) -> None:
        minted = await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="doomed"),
        )
        await controller.revoke(_request(tenant_id="acme"), minted.id)
        listing = await controller.list_tokens(_request(tenant_id="acme"))
        matching = [t for t in listing if t.id == minted.id]
        assert len(matching) == 1
        assert matching[0].revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_is_idempotent(self, controller: AgentTokensController) -> None:
        """Second revoke against the same id is still a no-op (204).

        The repository's ``revoke`` returns False the second time
        (already-revoked rows are skipped) -- the controller's
        contract says that's still a successful 204; only a
        missing-row case raises 404.
        """
        minted = await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="double-revoke"),
        )
        first = await controller.revoke(_request(tenant_id="acme"), minted.id)
        second = await controller.revoke(_request(tenant_id="acme"), minted.id)
        # 204 status is enforced by the decorator; both calls returning
        # None (no body) is the asserted contract here.
        assert first is None
        assert second is None

    @pytest.mark.asyncio
    async def test_revoke_unknown_raises_404(self, controller: AgentTokensController) -> None:
        with pytest.raises(ResourceNotFound):
            await controller.revoke(_request(tenant_id="acme"), "does-not-exist")

    @pytest.mark.asyncio
    async def test_revoke_cross_tenant_raises_404(self, controller: AgentTokensController) -> None:
        # Token belongs to acme; bcorp must not be able to revoke it.
        minted = await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="cross-tenant"),
        )
        with pytest.raises(ResourceNotFound):
            await controller.revoke(_request(tenant_id="bcorp"), minted.id)


class TestRemint:
    @pytest.mark.asyncio
    async def test_remint_same_name_succeeds(self, controller: AgentTokensController) -> None:
        """Names are not unique -- the same operator can mint a second
        token with the same human label and get a fresh secret back."""
        first = await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="duplicate"),
        )
        second = await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="duplicate"),
        )
        assert first.id != second.id
        assert first.prefix != second.prefix
        assert first.token != second.token


class TestCreatedAtUtc:
    """Sanity-check the persisted ``created_at`` is timezone-aware UTC.

    The service stamps ``datetime.now(UTC)`` on mint -- if anyone
    downgrades that to a naive timestamp, the DTO's ``datetime`` field
    silently accepts it and downstream consumers (audit, billing)
    break. Pin the contract here.
    """

    @pytest.mark.asyncio
    async def test_created_at_is_timezone_aware(self, controller: AgentTokensController) -> None:
        created = await controller.mint(
            _request(tenant_id="acme"),
            AgentTokenMintRequest(name="tz-check"),
        )
        assert created.created_at.tzinfo is not None
        # Sanity: the stamp is recent (within the last minute).
        delta = datetime.now(UTC) - created.created_at
        assert delta.total_seconds() < 60
