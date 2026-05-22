# Copyright 2026 Firefly Software Solutions Inc
"""``TenantContextMiddleware`` binds the request-scoped ContextVar.

Pyfly's ``@rest_controller`` resolver bypasses FastAPI's ``Depends``
chain, so :func:`require_tenant_context` -- the canonical
ContextVar-binding dependency -- never fires for pyfly-mounted
routes. Without a middleware to bind the ContextVar from headers,
the SQLAlchemy ``after_begin`` hook reads ``None`` and skips the
``SET LOCAL app.tenant_id`` GUC writes; every RLS-bound read through
a non-``BYPASSRLS`` role then returns zero rows (silently masked in
dev/test where the connection role bypasses RLS).

These tests exercise the middleware in isolation against a stubbed
Starlette :class:`Request` so the contract is pinned without spinning
up the pyfly DI graph. The full controller -> GUC -> RLS pipeline is
covered by ``tests/integration/test_rls_through_middleware.py`` under
a real Postgres testcontainer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.responses import JSONResponse

from flycanon.web.conventions.context import current_tenant_context
from flycanon.web.conventions.middleware import TenantContextMiddleware


def _request(headers: dict[str, str]) -> Any:
    """Build a Starlette-compatible request stub.

    The middleware only reads ``request.headers.get(...)``; a duck-
    typed object with a dict-like ``headers`` attribute is enough.
    """

    class _Headers:
        def __init__(self, data: dict[str, str]) -> None:
            self._data = data

        def get(self, key: str, default: str | None = None) -> str | None:
            return self._data.get(key, default)

    class _Request:
        def __init__(self, data: dict[str, str]) -> None:
            self.headers = _Headers(data)

    return _Request(headers)


def _middleware() -> TenantContextMiddleware:
    """The middleware's ``dispatch`` doesn't touch ``self.app``."""
    return TenantContextMiddleware(app=AsyncMock())


@pytest.mark.asyncio
async def test_binds_context_var_when_headers_present() -> None:
    """Valid tenant + workspace headers -> ContextVar bound inside the route."""
    captured: dict[str, Any] = {}

    async def _route(_: Any) -> JSONResponse:
        ctx = current_tenant_context()
        captured["ctx"] = ctx
        return JSONResponse({"ok": True})

    middleware = _middleware()
    request = _request(
        {
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Correlation-Id": "01J5XYZ",
        }
    )

    response = await middleware.dispatch(request, _route)
    assert response.status_code == 200

    bound = captured["ctx"]
    assert bound is not None
    assert bound.tenant_id == "acme"
    assert bound.workspace_id == "ws-q3"
    assert bound.correlation_id == "01J5XYZ"

    # After dispatch returns, the ContextVar is reset to None.
    assert current_tenant_context() is None


@pytest.mark.asyncio
async def test_skips_binding_when_headers_absent() -> None:
    """No tenant/workspace headers -> middleware no-ops cleanly.

    Health checks, ``/api/v1/setup/status``, ``/api/v1/version``, and
    OpenAPI doc routes go through here. The middleware must NOT raise
    and the ContextVar must remain unset so the route's own handling
    (or absence thereof) takes over.
    """
    captured: dict[str, Any] = {}

    async def _route(_: Any) -> JSONResponse:
        captured["ctx"] = current_tenant_context()
        return JSONResponse({"ok": True})

    middleware = _middleware()
    request = _request({})

    response = await middleware.dispatch(request, _route)
    assert response.status_code == 200
    assert captured["ctx"] is None
    assert current_tenant_context() is None


@pytest.mark.asyncio
async def test_skips_binding_when_headers_invalid() -> None:
    """Malformed slug (e.g. uppercase) -> no binding, no crash."""
    captured: dict[str, Any] = {}

    async def _route(_: Any) -> JSONResponse:
        captured["ctx"] = current_tenant_context()
        return JSONResponse({"ok": True})

    middleware = _middleware()
    # Uppercase fails the slug validator -> MissingTenantContext is
    # raised internally by tenant_context_from_headers and swallowed
    # by the middleware.
    request = _request(
        {
            "X-Tenant-Id": "ACME",  # not a valid slug
            "X-Workspace-Id": "ws-q3",
        }
    )

    response = await middleware.dispatch(request, _route)
    assert response.status_code == 200
    assert captured["ctx"] is None
    assert current_tenant_context() is None


@pytest.mark.asyncio
async def test_resets_token_on_route_exception() -> None:
    """Route raises -> ContextVar still reset (no cross-request leak)."""

    class _BoomError(RuntimeError):
        pass

    async def _route(_: Any) -> JSONResponse:
        # Sanity: the ContextVar is bound while the route runs.
        assert current_tenant_context() is not None
        raise _BoomError("route blew up")

    middleware = _middleware()
    request = _request(
        {
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
        }
    )

    with pytest.raises(_BoomError):
        await middleware.dispatch(request, _route)

    # Critical: the finally block must have reset the token even though
    # the route raised. Otherwise the next request on the same event
    # loop task would inherit the prior tenant scope.
    assert current_tenant_context() is None


@pytest.mark.asyncio
async def test_correlation_id_generated_when_absent() -> None:
    """Header-less correlation id is auto-generated by the headers helper."""
    captured: dict[str, Any] = {}

    async def _route(_: Any) -> JSONResponse:
        captured["ctx"] = current_tenant_context()
        return JSONResponse({"ok": True})

    middleware = _middleware()
    request = _request(
        {
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
        }
    )

    response = await middleware.dispatch(request, _route)
    assert response.status_code == 200

    bound = captured["ctx"]
    assert bound is not None
    assert isinstance(bound.correlation_id, str)
    assert len(bound.correlation_id) >= 8
