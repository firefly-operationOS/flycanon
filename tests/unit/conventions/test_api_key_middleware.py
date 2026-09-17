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

"""``ApiKeyMiddleware`` -- the gate that makes ``FLYCANON_API_KEYS`` real.

Before 26.7.1 the setting was parsed and consulted by nothing, so
these tests pin the contract at two levels:

* the pure routing / extraction / comparison helpers, so the rules
  (public version route, agent-token exemption, admin path, header
  forms, constant-time match) are each asserted in isolation;
* the middleware mounted on a real Starlette app driven through
  ``TestClient``, so the 401 body is proven to be the RFC 7807
  ``application/problem+json`` envelope every other refusal uses and
  a valid key is proven to reach the route.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from flycanon.config import CanonSettings
from flycanon.web.conventions.api_key_middleware import (
    ApiKeyMiddleware,
    ApiKeyPrincipalFilter,
    extract_api_key,
    key_matches,
    log_api_key_mode,
    path_requires_key,
    principal_for,
)


def _settings(keys: str | None) -> CanonSettings:
    return CanonSettings(api_keys=keys)


# ---------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------


class TestExtractApiKey:
    def test_x_api_key_header(self) -> None:
        assert extract_api_key({"X-API-Key": " k1 "}) == "k1"

    def test_authorization_apikey_scheme(self) -> None:
        assert extract_api_key({"Authorization": "ApiKey k2"}) == "k2"
        assert extract_api_key({"Authorization": "apikey   k2"}) == "k2"

    def test_bearer_is_not_an_api_key(self) -> None:
        # ``Bearer`` is the operator-JWT slot; it must never be mistaken
        # for the platform key.
        assert extract_api_key({"Authorization": "Bearer eyJ"}) is None

    def test_x_api_key_wins_over_authorization(self) -> None:
        assert extract_api_key({"X-API-Key": "k1", "Authorization": "ApiKey k2"}) == "k1"

    def test_empty_values_are_absent(self) -> None:
        assert extract_api_key({"X-API-Key": "   "}) is None
        assert extract_api_key({"Authorization": "ApiKey "}) is None
        assert extract_api_key({}) is None


class TestKeyMatches:
    def test_match_and_mismatch(self) -> None:
        assert key_matches("a", {"a", "b"}) is True
        assert key_matches("c", {"a", "b"}) is False
        assert key_matches("", {"a"}) is False

    def test_principal_is_a_fingerprint_not_the_key(self) -> None:
        principal = principal_for("super-secret")
        assert principal.startswith("api-key:")
        assert "super-secret" not in principal
        assert len(principal) == len("api-key:") + 8


class TestPathRequiresKey:
    def test_api_routes_are_gated(self) -> None:
        assert path_requires_key("/api/v1/sources", admin_path="/admin", has_agent_token=False)
        assert path_requires_key("/api/v1/agent-tokens", admin_path="/admin", has_agent_token=False)

    def test_version_is_public(self) -> None:
        assert not path_requires_key("/api/v1/version", admin_path="/admin", has_agent_token=False)

    def test_agent_route_with_token_is_exempt_without_token_gated(self) -> None:
        assert not path_requires_key("/api/v1/agent/sources", admin_path="/admin", has_agent_token=True)
        assert path_requires_key("/api/v1/agent/sources", admin_path="/admin", has_agent_token=False)

    def test_admin_path_is_gated_and_actuator_docs_are_not(self) -> None:
        assert path_requires_key("/admin", admin_path="/admin", has_agent_token=False)
        assert path_requires_key("/admin/api/health", admin_path="/admin/", has_agent_token=False)
        assert not path_requires_key("/administrator", admin_path="/admin", has_agent_token=False)
        for public in ("/actuator/health", "/actuator/health/readiness", "/docs", "/openapi.json", "/redoc"):
            assert not path_requires_key(public, admin_path="/admin", has_agent_token=False), public


class TestBootLog:
    def test_open_mode_warns_and_keyed_mode_informs(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="flycanon.web.conventions.api_key_middleware"):
            log_api_key_mode(_settings(None))
            log_api_key_mode(_settings("k1, k2"))
        messages = [(r.levelname, r.getMessage()) for r in caplog.records]
        assert any(lvl == "WARNING" and "api-key gate DISABLED" in msg for lvl, msg in messages)
        assert any(lvl == "INFO" and "api-key gate ENABLED: 2 key(s)" in msg for lvl, msg in messages)


# ---------------------------------------------------------------------
# Middleware on a real ASGI app
# ---------------------------------------------------------------------


def _app(keys: str | None) -> TestClient:
    async def echo(request: Request) -> JSONResponse:
        principal = getattr(request.state, "flycanon_principal", None)
        return JSONResponse({"path": request.url.path, "principal": principal})

    routes = [
        Route("/api/v1/version", echo),
        Route("/api/v1/sources", echo, methods=["GET", "POST"]),
        Route("/api/v1/agent/sources", echo, methods=["POST"]),
        Route("/admin/api/health", echo),
        Route("/actuator/health", echo),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(ApiKeyMiddleware, settings=_settings(keys), admin_path="/admin")
    return TestClient(app)


class TestMiddleware:
    def test_open_mode_passes_everything(self) -> None:
        client = _app(None)
        assert client.get("/api/v1/sources").status_code == 200
        assert client.get("/admin/api/health").status_code == 200

    def test_missing_key_is_401_problem_json(self) -> None:
        client = _app("k1")
        response = client.get("/api/v1/sources")
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["code"] == "missing_api_key"
        assert body["status"] == 401
        assert body["instance"] == "/api/v1/sources"

    def test_wrong_key_is_401_invalid(self) -> None:
        client = _app("k1")
        response = client.get("/api/v1/sources", headers={"X-API-Key": "nope"})
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_api_key"

    def test_valid_key_both_header_forms_reach_route_with_principal(self) -> None:
        client = _app("k1,k2")
        direct = client.get("/api/v1/sources", headers={"X-API-Key": "k1"})
        assert direct.status_code == 200
        assert direct.json()["principal"] == principal_for("k1")
        scheme = client.post("/api/v1/sources", headers={"Authorization": "ApiKey k2"})
        assert scheme.status_code == 200
        assert scheme.json()["principal"] == principal_for("k2")

    def test_version_actuator_stay_public_in_keyed_mode(self) -> None:
        client = _app("k1")
        assert client.get("/api/v1/version").status_code == 200
        assert client.get("/actuator/health").status_code == 200

    def test_agent_route_accepts_agent_token_without_platform_key(self) -> None:
        client = _app("k1")
        with_token = client.post("/api/v1/agent/sources", headers={"X-Agent-Token": "agt_x_y"})
        assert with_token.status_code == 200
        assert with_token.json()["principal"] is None
        without = client.post("/api/v1/agent/sources")
        assert without.status_code == 401
        assert without.json()["code"] == "missing_api_key"

    def test_admin_is_gated_in_keyed_mode(self) -> None:
        client = _app("k1")
        assert client.get("/admin/api/health").status_code == 401
        assert client.get("/admin/api/health", headers={"X-API-Key": "k1"}).status_code == 200


# ---------------------------------------------------------------------
# Admin bridge into pyfly's SecurityContext
# ---------------------------------------------------------------------


class TestPrincipalFilter:
    @pytest.mark.asyncio
    async def test_principal_populates_security_context(self) -> None:
        from pyfly.context.request_context import RequestContext

        RequestContext.init()
        seen: dict[str, object] = {}

        async def _next(_request: object) -> str:
            rc = RequestContext.current()
            seen["ctx"] = rc.security_context if rc else None
            return "ok"

        request = SimpleNamespace(state=SimpleNamespace(flycanon_principal="api-key:abcd1234"))
        try:
            assert await ApiKeyPrincipalFilter().do_filter(request, _next) == "ok"
        finally:
            RequestContext.clear()
        ctx = seen["ctx"]
        assert ctx is not None
        assert ctx.is_authenticated and ctx.user_id == "api-key:abcd1234"  # type: ignore[union-attr]
        assert ctx.has_role("ADMIN")  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_no_principal_leaves_context_untouched(self) -> None:
        from pyfly.context.request_context import RequestContext

        RequestContext.init()
        seen: dict[str, object] = {}

        async def _next(_request: object) -> str:
            rc = RequestContext.current()
            seen["ctx"] = rc.security_context if rc else None
            return "ok"

        request = SimpleNamespace(state=SimpleNamespace())
        try:
            await ApiKeyPrincipalFilter().do_filter(request, _next)
        finally:
            RequestContext.clear()
        assert seen["ctx"] is None
