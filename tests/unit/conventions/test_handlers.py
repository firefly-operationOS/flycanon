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

"""``register_exception_handlers(app)`` makes ``FireflyHTTPException`` map
to a ``ProblemDetail`` JSON response with the right HTTP status.

Notes:
- The response media type is ``application/problem+json`` (RFC 7807 §3).
- The handler reads ``current_tenant_context()`` so ``correlation_id``
  rides every response without each raising site repeating it.
- Pydantic ``ValidationError`` from request bodies maps to
  ``invalid_request`` with the per-field ``errors`` populated.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from flycanon.web.conventions.context import (
    TenantContext,
    set_tenant_context,
)
from flycanon.web.conventions.exceptions import (
    InvalidRequest,
    WorkspaceNotFound,
)
from flycanon.web.conventions.handlers import register_exception_handlers


# Defined at module scope so FastAPI can resolve the annotation under
# ``from __future__ import annotations`` (PEP 563 stringification). With a
# locally-scoped class, FastAPI falls back to treating ``body: Body`` as a
# query parameter and the validation error rides on ``loc=('query', 'body')``
# instead of ``loc=('body', 'name')``.
class Body(BaseModel):
    name: str


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raises_workspace_not_found")
    def _wnf() -> None:
        raise WorkspaceNotFound("Workspace 'ws_x' does not exist.")

    @app.get("/raises_invalid_with_errors")
    def _inv() -> None:
        raise InvalidRequest(
            "Validation failed.",
            errors=[{"code": "missing", "path": "name", "message": "required"}],
        )

    @app.post("/validates_body")
    def _v(body: Body) -> dict[str, str]:
        return {"ok": body.name}

    @app.get("/raises_with_correlation")
    async def _cwc() -> None:
        # Async route: ContextVar set here lives in the same task as the
        # exception handler. Sync routes run in a threadpool and the
        # ContextVar mutation would not propagate back to the handler --
        # which mirrors production where ``require_tenant_context`` will
        # be an async dependency.
        ctx = TenantContext(
            tenant_id="acme",
            workspace_id="ws",
            correlation_id="01J5XYZ",
        )
        set_tenant_context(ctx)
        raise WorkspaceNotFound("nope")

    return app


def test_workspace_not_found_renders_404_problem_json() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/raises_workspace_not_found")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 404
    assert body["code"] == "workspace_not_found"
    assert body["title"] == "Workspace not found"
    assert body["detail"] == "Workspace 'ws_x' does not exist."
    assert body["type"] == "https://firefly.dev/problems/workspace_not_found"


def test_invalid_request_renders_422_with_errors() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/raises_invalid_with_errors")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert body["errors"] == [{"code": "missing", "path": "name", "message": "required"}]


def test_pydantic_validation_error_renders_invalid_request() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.post("/validates_body", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert body["title"] == "Invalid request"
    assert len(body["errors"]) >= 1
    e0 = body["errors"][0]
    assert {"code", "path", "message"}.issubset(e0)
    # path is normalized: location-source prefix (body/query/path/header/cookie)
    # is stripped — callers branch on field name, not on where it lived.
    assert e0["path"] == "name"


def test_correlation_id_propagated_from_context() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/raises_with_correlation")
    body = response.json()
    assert body["correlation_id"] == "01J5XYZ"
