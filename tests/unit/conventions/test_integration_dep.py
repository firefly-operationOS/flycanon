# Copyright 2026 Firefly Software Solutions Inc
"""Integration tests for the require_tenant_context dependency.

These tests cover the path the unit tests do not exercise: the
dependency wires the ContextVar, the route reads it via
``current_tenant_context()``, and ``tenant_safe_client`` propagates
it on an outbound call. This is the path the controllers use -- it
MUST work end-to-end.
"""

from __future__ import annotations

import httpx
import respx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from flycanon.web.conventions.context import current_tenant_context
from flycanon.web.conventions.deps import require_tenant_context
from flycanon.web.conventions.handlers import register_exception_handlers
from flycanon.web.conventions.http_client import tenant_safe_client


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/ctx-readback")
    async def _readback(_: object = Depends(require_tenant_context)) -> dict:
        ctx = current_tenant_context()
        assert ctx is not None
        return {
            "tenant": ctx.tenant_id,
            "workspace": ctx.workspace_id,
            "correlation": ctx.correlation_id,
        }

    @app.get("/outbound")
    async def _outbound(_: object = Depends(require_tenant_context)) -> dict:
        async with tenant_safe_client(base_url="https://upstream.example") as client:
            response = await client.get("/ping")
        return {"upstream_status": response.status_code}

    return app


def _client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


def test_dep_binds_contextvar_visible_to_route() -> None:
    response = _client().get(
        "/ctx-readback",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Correlation-Id": "01J5INT",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "tenant": "acme",
        "workspace": "ws-q3",
        "correlation": "01J5INT",
    }


@respx.mock
def test_dep_enables_tenant_safe_outbound_call() -> None:
    route = respx.get("https://upstream.example/ping").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    response = _client().get(
        "/outbound",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Correlation-Id": "01J5INT",
        },
    )
    assert response.status_code == 200
    assert route.called
    request_headers = route.calls.last.request.headers
    assert request_headers["X-Tenant-Id"] == "acme"
    assert request_headers["X-Workspace-Id"] == "ws-q3"
    assert request_headers["X-Correlation-Id"] == "01J5INT"
