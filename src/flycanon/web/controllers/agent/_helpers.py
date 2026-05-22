# Copyright 2026 Firefly Software Solutions Inc
"""Shared helpers for the agent-tier controllers.

Four helpers, all kept intentionally minimal:

* :func:`verify_agent_token` -- the per-route auth gate. Reads the
  ``X-Agent-Token`` header, calls :meth:`AgentTokenService.verify`
  against the resolved tenant context, and returns a fresh
  :class:`TenantContext` whose ``actor`` is the verified agent
  actor string (``"agent:<prefix>"``).
* :func:`require_idempotency_key` -- mandatory-header check for
  POST routes. Agent-tier POSTs MUST carry ``Idempotency-Key``.
  Missing key returns ``400 missing_idempotency_key``.
* :func:`check_idempotency_replay` -- replay-dedup read. After the
  mandatory-header check passes, this looks up the
  ``(tenant_id, scope, key)`` tuple in the
  :class:`IdempotencyStore`. If a non-expired entry exists, the
  caller returns the cached response without re-dispatching the
  command. Otherwise it falls through to dispatch.
* :func:`store_idempotent_response` -- the corresponding write
  surface. After a successful dispatch, the controller stashes
  the response status + JSON body under the same triple so a
  retry within the TTL replays the original response.

The (tenant, scope) namespacing is enforced here, not in the store
-- the store treats ``scope`` as an opaque route identifier so two
distinct agent endpoints sharing the same ``Idempotency-Key`` do
not collide.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from starlette.requests import Request

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.web.conventions import (
    HEADER_AGENT_TOKEN,
    HEADER_IDEMPOTENCY_KEY,
    HEADER_TENANT_ID,
    IdempotencyStore,
    MissingIdempotencyKey,
    StoredResponse,
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


async def check_idempotency_replay(
    http_request: Request,
    store: IdempotencyStore,
    scope: str,
) -> StoredResponse | None:
    """Look up an existing response under the ``(tenant, scope, key)`` tuple.

    Workflow on every agent POST:

    1. Calls :func:`require_idempotency_key` so the header presence
       gate fires before any store lookup (preserves the existing
       400 behaviour for callers who forget the header).
    2. Reads :data:`HEADER_TENANT_ID` from the request headers --
       a missing tenant header would already have failed the
       :func:`verify_agent_token` gate, so we don't re-raise here.
       An empty string falls through and the store treats it as a
       distinct namespace (i.e. an anonymous tenant cannot collide
       with ``"acme"``).
    3. Awaits :meth:`IdempotencyStore.lookup` with the namespaced
       tuple. Returns the stored response if the entry exists, is
       not expired, and carries a recorded response body.

    Returns the cached :class:`StoredResponse` if found, else
    ``None`` so the controller falls through to dispatch.
    Raises :class:`MissingIdempotencyKey` if the header is absent.

    Async to honour the :class:`IdempotencyStore` Protocol -- the
    in-memory store's body is sync but the Redis adapter awaits
    the underlying client, so callers MUST await this helper.
    """
    key = require_idempotency_key(http_request)
    tenant_id = http_request.headers.get(HEADER_TENANT_ID, "")
    return await store.lookup(tenant_id=tenant_id, route=scope, key=key)


def _to_json_body(response: Any) -> dict[str, Any] | list[Any]:
    """Normalise a controller response into a JSON-serialisable body.

    Three shapes the agent controllers actually emit:

    * pydantic ``BaseModel`` -- e.g. :class:`SourceRecord`,
      :class:`AnswerResponse`. ``model_dump(mode="json")`` produces
      the wire-equivalent dict.
    * ``list`` of ``BaseModel`` -- e.g. ``list[CandidateRecord]``
      from the propose endpoint. Each item is dumped individually.
    * raw ``dict`` / ``list`` -- the test suites' stubbed
      handlers occasionally return strings or sentinels; those
      are passed through unchanged so the store accepts the
      mock contract without forcing every test fixture to switch
      to real models.
    """
    if isinstance(response, BaseModel):
        return response.model_dump(mode="json")
    if isinstance(response, list):
        if response and isinstance(response[0], BaseModel):
            return [item.model_dump(mode="json") for item in response]
        return response
    if isinstance(response, dict):
        return response
    # Sentinels (strings, None) used by the existing stubbed
    # controller tests -- wrap them in a 1-key dict so the store
    # contract (``dict | list``) stays honest while the older test
    # surface keeps working without a wholesale refactor.
    return {"value": response}


async def store_idempotent_response(
    http_request: Request,
    store: IdempotencyStore,
    scope: str,
    *,
    status: int,
    response: Any,
) -> None:
    """Persist the route's response under ``(tenant, scope, key)`` for replay dedup.

    Called once on the happy path after the command bus dispatch
    succeeds. The key + tenant are pulled from the request headers
    rather than re-validated -- the gate already ran and raising a
    second time would mask the real dispatch result. An empty key
    is a no-op (the store guards against this defensively).

    ``response`` accepts any of the shapes the agent controllers
    emit (pydantic ``BaseModel``, ``list[BaseModel]``, or a raw
    JSON-serialisable container); :func:`_to_json_body` normalises
    it for the store.

    Async to honour the :class:`IdempotencyStore` Protocol -- the
    in-memory store's body is sync but the Redis adapter awaits
    the underlying client, so callers MUST await this helper.
    """
    key = http_request.headers.get(HEADER_IDEMPOTENCY_KEY) or ""
    tenant_id = http_request.headers.get(HEADER_TENANT_ID, "")
    await store.record_response(
        tenant_id=tenant_id,
        route=scope,
        key=key,
        status=status,
        json_body=_to_json_body(response),
    )


__all__ = [
    "check_idempotency_replay",
    "require_idempotency_key",
    "store_idempotent_response",
    "verify_agent_token",
]
