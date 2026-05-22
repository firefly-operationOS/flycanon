# Copyright 2026 Firefly Software Solutions Inc
"""``require_tenant_context`` parses headers into a ``TenantContext``.

This task covers the no-JWT path (tenant/workspace/correlation
headers only). JWT cross-check is added in Task 8.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from flycanon.web.conventions.context import TenantContext
from flycanon.web.conventions.deps import require_tenant_context
from flycanon.web.conventions.handlers import register_exception_handlers


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/who")
    def _who(
        ctx: TenantContext = Depends(require_tenant_context),  # noqa: B008
    ) -> dict:
        return {
            "tenant": ctx.tenant_id,
            "workspace": ctx.workspace_id,
            "actor": ctx.actor,
            "correlation": ctx.correlation_id,
        }

    return app


def _client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


def test_happy_path_returns_context() -> None:
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Correlation-Id": "01J5XYZ",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "tenant": "acme",
        "workspace": "ws-q3",
        "actor": None,
        "correlation": "01J5XYZ",
    }


def test_missing_tenant_header_returns_400() -> None:
    response = _client().get("/who", headers={"X-Workspace-Id": "ws"})
    assert response.status_code == 400
    assert response.json()["code"] == "missing_tenant_context"


def test_missing_workspace_header_returns_400() -> None:
    response = _client().get("/who", headers={"X-Tenant-Id": "acme"})
    assert response.status_code == 400
    assert response.json()["code"] == "missing_tenant_context"


def test_invalid_slug_returns_400_invalid_request() -> None:
    response = _client().get(
        "/who",
        headers={"X-Tenant-Id": "ACME", "X-Workspace-Id": "ws"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] in {"missing_tenant_context", "invalid_request"}
    # Either rejection style is acceptable; assert the response is a
    # problem detail with the right shape:
    assert body["title"]
    assert body["status"] == 400


def test_correlation_id_generated_if_absent() -> None:
    response = _client().get(
        "/who",
        headers={"X-Tenant-Id": "acme", "X-Workspace-Id": "ws"},
    )
    assert response.status_code == 200
    correlation = response.json()["correlation"]
    assert isinstance(correlation, str) and len(correlation) >= 8


# --- JWT cross-check tests (Task 8) ---------------------------------


def _decoded_jwt_header(payload: dict, *, alg: str = "none") -> str:
    """Build an ``Authorization: Bearer <jwt>`` value carrying ``payload``.

    The signature is empty — these tests do not exercise verification;
    the conventions module trusts the decoded payload.
    """

    import base64
    import json

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(payload).encode())
    return f"Bearer {header}.{body}."


def test_jwt_populates_actor() -> None:
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "Authorization": _decoded_jwt_header({"sub": "alice", "tenant": "acme"}),
        },
    )
    assert response.status_code == 200
    assert response.json()["actor"] == "user:alice"


def test_jwt_tenant_claim_mismatch_returns_403() -> None:
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "Authorization": _decoded_jwt_header({"sub": "alice", "tenant": "bcorp"}),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_claim_mismatch"


def test_agent_token_populates_actor() -> None:
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "X-Agent-Token": "agt_abc12345_secretpart",
        },
    )
    assert response.status_code == 200
    assert response.json()["actor"] == "agent:agt_abc12345"


def test_malformed_jwt_payload_does_not_crash() -> None:
    """Non-dict JWT payload must not raise 500 — handled as no actor.

    The conventions module never authenticates anyway; an adversarial
    Bearer header just means "no actor", not "server fault".
    """

    import base64

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = b64(b'{"alg":"none"}')
    bad_payload = b64(b"[1,2,3]")
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "acme",
            "X-Workspace-Id": "ws-q3",
            "Authorization": f"Bearer {header}.{bad_payload}.",
        },
    )
    assert response.status_code == 200
    assert response.json()["actor"] is None


def test_jwt_empty_sub_with_mismatched_tenant_still_rejected() -> None:
    """Bypass case 1: sub='' previously returned None Actor, so the
    tenant-claim check was skipped. Must now reject."""
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "victim",
            "X-Workspace-Id": "ws",
            "Authorization": _decoded_jwt_header({"sub": "", "tenant": "otherco"}),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_claim_mismatch"


def test_jwt_non_string_tenant_claim_rejected() -> None:
    """Bypass case 2: tenant=42 was previously coerced to None and
    the check was skipped. Must now reject."""
    response = _client().get(
        "/who",
        headers={
            "X-Tenant-Id": "victim",
            "X-Workspace-Id": "ws",
            "Authorization": _decoded_jwt_header({"sub": "alice", "tenant": 42}),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_claim_mismatch"
