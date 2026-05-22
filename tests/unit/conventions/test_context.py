# Copyright 2026 Firefly Software Solutions Inc
"""``TenantContext`` is the request-scoped (tenant, workspace, actor) tuple.

It is set by the FastAPI dependency at request start and read
anywhere downstream — services, repositories, the tenant-safe
HTTP client — via a ContextVar.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from flycanon.web.conventions.context import (
    TenantContext,
    current_tenant_context,
    set_tenant_context,
)


def test_tenant_context_immutable() -> None:
    ctx = TenantContext(
        tenant_id="acme",
        workspace_id="ws-q3",
        actor="user:alice",
        correlation_id="01J5XYZ",
    )
    with pytest.raises(ValidationError):
        ctx.tenant_id = "evil"  # type: ignore[misc]


def test_tenant_context_rejects_bad_slug() -> None:
    with pytest.raises(ValidationError):
        TenantContext(
            tenant_id="ACME",
            workspace_id="ws-q3",
            actor=None,
            correlation_id="01J5XYZ",
        )


def test_tenant_context_actor_optional() -> None:
    ctx = TenantContext(
        tenant_id="acme",
        workspace_id="ws-q3",
        actor=None,
        correlation_id="01J5XYZ",
    )
    assert ctx.actor is None


def test_tenant_context_actor_defaults_to_none() -> None:
    ctx = TenantContext(
        tenant_id="acme",
        workspace_id="ws-q3",
        correlation_id="01J5XYZ",
    )
    assert ctx.actor is None


def test_current_tenant_context_returns_none_outside_request() -> None:
    assert current_tenant_context() is None


def test_set_tenant_context_isolation_across_tasks() -> None:
    """Each asyncio task gets its own context — no cross-leak."""

    captured: dict[str, TenantContext | None] = {}

    async def worker(label: str, tenant_id: str) -> None:
        ctx = TenantContext(
            tenant_id=tenant_id,
            workspace_id="ws",
            actor=None,
            correlation_id=label,
        )
        token = set_tenant_context(ctx)
        try:
            await asyncio.sleep(0)
            captured[label] = current_tenant_context()
        finally:
            token.var.reset(token.token)  # restore

    async def main() -> None:
        await asyncio.gather(worker("a", "acme"), worker("b", "bcorp"))

    asyncio.run(main())

    assert captured["a"] is not None
    assert captured["a"].tenant_id == "acme"
    assert captured["b"] is not None
    assert captured["b"].tenant_id == "bcorp"
