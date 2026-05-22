# Copyright 2026 Firefly Software Solutions Inc
"""Agent-tier REST controllers (``/api/v1/agent/*``).

Each controller in this package is a thin wrapper around the
existing user-tier CQRS handlers, gated by ``X-Agent-Token``
instead of an operator JWT. The business logic stays exactly
where the user-tier controllers leave it: agent routes resolve a
verified :class:`flycanon.web.conventions.TenantContext` and
dispatch the same commands / queries the user-tier surface uses.

Pyfly's :func:`pyfly.container.rest_controller` does not process
FastAPI ``Depends()`` defaults, so each route here mirrors the
pattern used by the user-tier controllers: inject the raw
Starlette ``Request``, parse the canonical headers via
:func:`flycanon.web.conventions.tenant_context_from_request`,
then call :meth:`AgentTokenService.verify` directly (through the
shared :func:`_helpers.verify_agent_token` helper) to enforce the
per-route scope.
"""
