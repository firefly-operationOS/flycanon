# Copyright 2026 Firefly Software Solutions Inc
"""Agent token mint + verify + list + revoke.

Tokens are stored as ``agt_<8hex>_<32hex>``; the first 12 chars
(``agt_<8hex>``) are the public prefix used as the lookup key,
the trailing 32 hex chars are the secret. Only the SHA-256 hash
of the full token is persisted.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from flycanon.web.conventions.exceptions import FireflyHTTPException

# -- Concrete exceptions exposed to controllers ---------------------


class InvalidAgentToken(FireflyHTTPException):
    status = 403
    code = "invalid_agent_token"
    title = "Invalid agent token"


class AgentTokenExpired(FireflyHTTPException):
    status = 403
    code = "agent_token_expired"
    title = "Agent token expired"


class AgentWorkspaceNotInAllowlist(FireflyHTTPException):
    status = 403
    code = "agent_workspace_not_in_allowlist"
    title = "Agent workspace not in allowlist"


class AgentScopeDenied(FireflyHTTPException):
    status = 403
    code = "agent_scope_denied"
    title = "Agent scope denied"


class AgentCannotMint(FireflyHTTPException):
    """Raised when an agent-tier caller tries to mint a token.

    Agents authenticate via ``X-Agent-Token`` (verified by
    :func:`require_agent_token`) and are never authorised to mint
    new tokens for other agents -- minting is a user-tier
    operation that requires an interactive operator JWT.
    """

    status = 403
    code = "agent_cannot_mint"
    title = "Agent cannot mint"


# -- Domain types ---------------------------------------------------


@dataclass(frozen=True)
class MintRequest:
    tenant_id: str
    name: str
    workspace_allowlist: list[str] | None
    scopes: list[str]
    rate_limit_rpm: int | None
    expires_at: datetime | None


@dataclass(frozen=True)
class MintedAgentToken:
    id: str
    prefix: str
    token: str  # raw, returned ONCE


@dataclass(frozen=True)
class AgentTokenSummary:
    id: str
    tenant_id: str
    name: str
    prefix: str
    workspace_allowlist: list[str] | None
    scopes: list[str]
    rate_limit_rpm: int | None
    expires_at: datetime | None
    created_at: datetime
    created_by: str
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True)
class VerifiedAgentToken:
    token_id: str
    actor: str
    prefix: str


# -- Repository protocol -------------------------------------------


class AgentTokenRepository(Protocol):
    """Persistence contract used by :class:`AgentTokenService`.

    Implemented by
    :class:`flycanon.models.repositories.agent_token_repository.AgentTokenRepository`
    for production; the unit tests substitute an in-memory double.
    """

    async def insert(self, row: dict) -> None: ...

    async def get_by_prefix(self, prefix: str) -> dict | None: ...

    async def list_for_tenant(self, tenant_id: str) -> list[dict]: ...

    async def revoke(self, token_id: str, *, at: datetime) -> bool: ...

    async def mark_used(self, token_id: str, *, at: datetime) -> None: ...


# -- Service --------------------------------------------------------


def _generate_token() -> tuple[str, str]:
    """Return ``(token, prefix)``. Token shape: ``agt_<8hex>_<32hex>``."""
    prefix_part = secrets.token_hex(4)  # 8 hex chars
    secret_part = secrets.token_hex(16)  # 32 hex chars
    prefix = f"agt_{prefix_part}"
    return f"{prefix}_{secret_part}", prefix


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AgentTokenService:
    """Mint / verify / list / revoke long-lived agent bearer tokens.

    The raw token is returned to the caller exactly once at mint time;
    only the SHA-256 hash plus the public 12-char prefix are
    persisted. ``verify`` is the hot path used by the
    ``require_agent_token`` FastAPI dependency: it looks the row up by
    prefix, constant-equality compares the full-token hash, then
    enforces tenant / expiry / workspace-allowlist / scope in that
    order.
    """

    def __init__(self, repo: AgentTokenRepository) -> None:
        self._repo = repo

    async def mint(self, request: MintRequest, *, actor: str) -> MintedAgentToken:
        token, prefix = _generate_token()
        token_id = uuid4().hex
        row = {
            "id": token_id,
            "tenant_id": request.tenant_id,
            "name": request.name,
            "prefix": prefix,
            "secret_hash": _hash(token),
            "workspace_allowlist_json": request.workspace_allowlist,
            "scopes_json": request.scopes,
            "rate_limit_rpm": request.rate_limit_rpm,
            "expires_at": request.expires_at,
            "created_at": datetime.now(UTC),
            "created_by": actor,
            "revoked_at": None,
            "last_used_at": None,
        }
        await self._repo.insert(row)
        return MintedAgentToken(id=token_id, prefix=prefix, token=token)

    async def verify(
        self,
        token: str,
        *,
        tenant_id: str,
        workspace_id: str,
        scope: str,
    ) -> VerifiedAgentToken:
        if not token.startswith("agt_") or len(token) < 12:
            raise InvalidAgentToken("Token shape is invalid.")
        prefix = token[:12]
        row = await self._repo.get_by_prefix(prefix)
        if row is None:
            raise InvalidAgentToken("Unknown agent token.")
        if row["tenant_id"] != tenant_id:
            raise InvalidAgentToken("Token does not match the tenant header.")
        if not secrets.compare_digest(row["secret_hash"], _hash(token)):
            raise InvalidAgentToken("Token signature mismatch.")
        expires = row.get("expires_at")
        if expires is not None and expires <= datetime.now(UTC):
            raise AgentTokenExpired(f"Token expired at {expires.isoformat()}.")
        allowlist = row.get("workspace_allowlist_json")
        if allowlist is not None and workspace_id not in allowlist:
            raise AgentWorkspaceNotInAllowlist(f"Token is not allowed for workspace {workspace_id!r}.")
        scopes = row.get("scopes_json") or []
        if "*" not in scopes and scope not in scopes:
            raise AgentScopeDenied(f"Token scope does not permit {scope!r}.")
        now = datetime.now(UTC)
        last_used = row.get("last_used_at")
        if last_used is None or (now - last_used).total_seconds() > 60:
            await self._repo.mark_used(row["id"], at=now)
        return VerifiedAgentToken(token_id=row["id"], actor=f"agent:{prefix}", prefix=prefix)

    async def list_for_tenant(self, tenant_id: str) -> list[AgentTokenSummary]:
        rows = await self._repo.list_for_tenant(tenant_id)
        return [
            AgentTokenSummary(
                id=row["id"],
                tenant_id=row["tenant_id"],
                name=row["name"],
                prefix=row["prefix"],
                workspace_allowlist=row.get("workspace_allowlist_json"),
                scopes=row.get("scopes_json") or [],
                rate_limit_rpm=row.get("rate_limit_rpm"),
                expires_at=row.get("expires_at"),
                created_at=row["created_at"],
                created_by=row["created_by"],
                revoked_at=row.get("revoked_at"),
                last_used_at=row.get("last_used_at"),
            )
            for row in rows
        ]

    async def revoke(self, token_id: str) -> bool:
        return await self._repo.revoke(token_id, at=datetime.now(UTC))
