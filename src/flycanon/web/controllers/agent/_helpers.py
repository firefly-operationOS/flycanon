# Copyright 2026 Firefly Software Solutions Inc
"""Shared helpers for the agent-tier controllers.

Two helpers, both lifted from the flyradar agent surface and kept
intentionally minimal:

* :func:`verify_agent_token` -- the per-route auth gate. Reads the
  ``X-Agent-Token`` header, calls :meth:`AgentTokenService.verify`
  against the resolved tenant context, and returns a fresh
  :class:`TenantContext` whose ``actor`` is the verified agent
  actor string (``"agent:<prefix>"``). Raises one of the typed
  :class:`FireflyHTTPException` subclasses surfaced by the service
  (invalid token, expired token, workspace not in allowlist,
  scope denied) -- those propagate to the global handler.

* :func:`require_idempotency_key` -- mandatory-header check for
  POST routes. Agent-tier POSTs MUST carry ``Idempotency-Key``
  (unlike the user-tier counterparts, which allow it to default
  to ``None``). Missing key returns ``400 missing_idempotency_key``.
"""

from __future__ import annotations

from starlette.requests import Request

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.web.conventions import (
    HEADER_AGENT_TOKEN,
    HEADER_IDEMPOTENCY_KEY,
    MissingIdempotencyKey,
    TenantContext,
    tenant_context_from_request,
)


async def verify_agent_token(
    http_request: Request,
    *,
    service: AgentTokenService,
    scope: str,
) -> TenantContext:
    """Verify ``X-Agent-Token`` and return a scope-refined context.

    Resolves the canonical tenant context from the request headers
    (raises :class:`MissingTenantContext` on missing / invalid
    tenant or workspace headers, exactly like the user-tier
    surface), then verifies the ``X-Agent-Token`` header against
    ``service.verify(...)`` with the per-route ``scope``.

    The returned :class:`TenantContext` is a fresh dataclass copy
    of the parsed context with ``actor`` overwritten by the
    verified token's actor string (``"agent:<prefix>"``). The
    header parser produced a prefix-only actor, but verifying the
    token's hash + scope + allowlist before trusting it is what
    distinguishes the agent surface from a plain header inspector.

    Raises:
        :class:`MissingAgentToken` -- ``X-Agent-Token`` header is
          absent (401).
        :class:`InvalidAgentToken` -- token shape is malformed, the
          prefix is unknown, the tenant does not match, or the
          stored hash does not match (403).
        :class:`AgentTokenExpired` -- ``expires_at`` is in the past
          (403).
        :class:`AgentWorkspaceNotInAllowlist` -- the token's
          allowlist is non-empty and does not include
          ``X-Workspace-Id`` (403).
        :class:`AgentScopeDenied` -- the token's scopes do not
          include ``scope`` (and do not include ``"*"``) (403).
    """
    # Lazy import to dodge a circular import at module load:
    # ``flycanon.web.agent_deps`` imports the conventions module,
    # which is already imported above.
    from flycanon.web.agent_deps import MissingAgentToken

    ctx = tenant_context_from_request(http_request)
    token = http_request.headers.get(HEADER_AGENT_TOKEN)
    if not token:
        raise MissingAgentToken("X-Agent-Token header is required for /api/v1/agent/* routes.")
    verified = await service.verify(
        token,
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        scope=scope,
    )
    return TenantContext(
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        actor=verified.actor,
        correlation_id=ctx.correlation_id,
    )


def require_idempotency_key(http_request: Request) -> str:
    """Return the ``Idempotency-Key`` header value or raise.

    Agent-tier POST routes mandate the header (unlike the user-tier
    POSTs, which allow it to default to ``None``). Missing key
    raises :class:`MissingIdempotencyKey` (400) so the gate
    response is uniform across the agent surface.
    """
    value = http_request.headers.get(HEADER_IDEMPOTENCY_KEY)
    if not value:
        raise MissingIdempotencyKey("Idempotency-Key header is required for /api/v1/agent/* POST routes.")
    return value


__all__ = ["require_idempotency_key", "verify_agent_token"]
