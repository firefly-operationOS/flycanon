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

"""``ApiKeyMiddleware`` -- the user-tier authentication gate.

History
=======

``FLYCANON_API_KEYS`` existed in :class:`CanonSettings` since the first
release and the deployment guide promised it gated ``/api/v1/*``. It
did not: the setting was parsed into ``api_key_set`` and read by
nothing. Anyone who could reach port 8500 could read or write any
tenant by choosing ``X-Tenant-Id``, and could mint agent tokens for
any tenant through ``POST /api/v1/agent-tokens``. A multi-tenant
caller (dworkers, wave six) found the gap while wiring flycanon as
its knowledge backend. This middleware is the enforcement the docs
already described.

Contract
========

* When ``settings.api_key_set`` is **empty** the service runs in
  *open* mode exactly as before: the middleware is a pass-through and
  :func:`log_api_key_mode` says so loudly at boot. Open mode exists
  for local development and for deployments that isolate the port on
  a private network; it is never the right choice on a shared host.
* When at least one key is configured, every request under
  ``/api/v1/`` and under the admin path must present a key, either
  as ``X-API-Key: <key>`` or as ``Authorization: ApiKey <key>``.
  A missing key renders ``401 missing_api_key``; an unknown key
  renders ``401 invalid_api_key``. Both are RFC 7807
  ``application/problem+json`` bodies, the same envelope every other
  refusal uses, so callers dispatch on ``code``.
* ``GET /api/v1/version`` stays public: it is the smoke probe
  operators and health dashboards hit before they hold a credential,
  and it discloses only the deployed CalVer + model ids.
* ``/api/v1/agent/*`` requests that carry ``X-Agent-Token`` pass
  through **without** a platform key. The agent tier has its own
  credential -- a tenant-scoped, hashed, scope-limited token that
  every agent route verifies itself -- and requiring the platform key
  on top would force every agent to hold the operator secret, which
  is precisely what agent tokens exist to avoid. An agent route
  without ``X-Agent-Token`` is gated like any other route (and would
  fail at the route with ``401 missing_agent_token`` anyway).
* The actuator, ``/docs``, ``/redoc`` and ``/openapi.json`` are not
  under either prefix and remain reachable; readiness probes must not
  need a secret.

Why a Starlette middleware and not a pyfly ``WebFilter``
========================================================

pyfly mounts its ``WebFilterChainMiddleware`` inside the FastAPI app,
so a filter runs *after* every ``app.add_middleware(...)`` layer and
can be appended to at runtime by bean discovery. The gate must be the
first thing a request meets and must not depend on bean scanning
succeeding, so it is an outer Starlette middleware. One consequence
shapes the code: a Starlette middleware sits *outside*
``ExceptionMiddleware``, so raising :class:`FireflyHTTPException`
here would bypass the registered handlers and surface as a bare 500.
The middleware therefore *renders* the problem response itself
through the same helpers the handlers use.

Comparison is constant-time (:func:`hmac.compare_digest`) against
every configured key so a timing side channel cannot narrow the key
space one byte at a time.

The admin bridge
================

pyfly's admin dashboard honours ``pyfly.admin.require-auth`` by
reading the request-scoped ``SecurityContext`` -- which flycanon
never populates because it has no JWT security filter. Turning
``require-auth`` on would therefore lock the dashboard for everyone,
key or no key. :class:`ApiKeyPrincipalFilter` closes that loop: the
middleware records the authenticated principal on
``request.state``, and the filter (which runs inside pyfly's chain,
after ``RequestContextFilter`` has created the context) copies it
into a ``SecurityContext`` with the ``ADMIN`` role. With keys
configured the dashboard answers to a valid key and refuses
everything else; with no keys it stays locked unless the operator
also sets ``FLYCANON_ADMIN_REQUIRE_AUTH=false`` (see ``pyfly.yaml``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING, Any

from pyfly.container.ordering import HIGHEST_PRECEDENCE
from pyfly.web.filters import OncePerRequestFilter
from pyfly.web.ports.filter import CallNext
from starlette.middleware.base import BaseHTTPMiddleware

from flycanon.config import CanonSettings, get_settings
from flycanon.web.conventions.exceptions import (
    FireflyHTTPException,
    InvalidApiKey,
    MissingApiKey,
)
from flycanon.web.conventions.handlers import problem_response_for
from flycanon.web.conventions.headers import (
    API_KEY_AUTH_SCHEME,
    HEADER_AGENT_TOKEN,
    HEADER_API_KEY,
    HEADER_AUTHORIZATION,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)

#: Prefix under which every tenant route lives. The gate applies to
#: everything below it except :data:`PUBLIC_PATHS`.
API_PREFIX = "/api/v1/"
#: Agent-tier prefix; requests here that carry ``X-Agent-Token`` are
#: authenticated by the route, not by the platform key.
AGENT_PREFIX = "/api/v1/agent/"
#: Paths under :data:`API_PREFIX` that never require a key.
PUBLIC_PATHS: frozenset[str] = frozenset({"/api/v1/version"})
#: ``request.state`` attribute the middleware sets on success; read by
#: :class:`ApiKeyPrincipalFilter`.
PRINCIPAL_STATE_ATTR = "flycanon_principal"
#: Role granted to an API-key principal for pyfly's admin gate
#: (``pyfly.admin.allowed-roles`` defaults to ``["ADMIN"]``).
API_KEY_ROLE = "ADMIN"


def extract_api_key(headers: Any) -> str | None:
    """Return the presented key from ``X-API-Key`` or ``Authorization: ApiKey``.

    ``X-API-Key`` wins when both are present -- it is the unambiguous
    slot -- and an ``Authorization`` header with any other scheme
    (``Bearer`` is the operator-JWT slot) yields ``None`` so the two
    credentials never shadow each other.
    """
    direct = headers.get(HEADER_API_KEY)
    if direct and direct.strip():
        return direct.strip()
    authorization = headers.get(HEADER_AUTHORIZATION)
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != API_KEY_AUTH_SCHEME.lower():
        return None
    value = value.strip()
    return value or None


def key_matches(presented: str, configured: set[str]) -> bool:
    """Constant-time membership test of ``presented`` in ``configured``.

    Every configured key is compared, never short-circuiting on the
    first match, so the time taken does not reveal which (if any) key
    matched.
    """
    matched = False
    for candidate in configured:
        if hmac.compare_digest(presented.encode("utf-8"), candidate.encode("utf-8")):
            matched = True
    return matched


def principal_for(key: str) -> str:
    """Actor label for a validated key: ``api-key:<first 8 hex of sha256>``.

    The fingerprint identifies *which* key was used in logs and audit
    rows without ever writing the secret itself anywhere.
    """
    return "api-key:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def path_requires_key(path: str, *, admin_path: str, has_agent_token: bool) -> bool:
    """Decide whether ``path`` is behind the platform key.

    Pure function so the routing rules are unit-testable without a
    Starlette request.
    """
    if path.startswith(API_PREFIX):
        if path in PUBLIC_PATHS:
            return False
        # An agent-tier request carrying its own credential is verified
        # by the route, not by the platform key.
        return not (path.startswith(AGENT_PREFIX) and has_agent_token)
    admin = admin_path.rstrip("/")
    return bool(admin) and (path == admin or path.startswith(admin + "/"))


def log_api_key_mode(settings: CanonSettings) -> None:
    """Say at boot, unmistakably, whether the user tier is gated.

    Silence here is how the old dead setting went unnoticed for months:
    operators set ``FLYCANON_API_KEYS`` and assumed it worked. The two
    messages are phrased so a log search for ``api-key gate`` answers
    the question either way.
    """
    keys = settings.api_key_set
    if keys:
        logger.info(
            "api-key gate ENABLED: %d key(s) loaded from FLYCANON_API_KEYS; "
            "/api/v1/* (except /api/v1/version) and the admin dashboard require "
            "X-API-Key or 'Authorization: ApiKey <key>'; /api/v1/agent/* accepts "
            "X-Agent-Token instead",
            len(keys),
        )
        return
    logger.warning(
        "api-key gate DISABLED: FLYCANON_API_KEYS is empty, so every /api/v1/* "
        "route accepts any caller for any tenant. Acceptable only on a private "
        "network; set FLYCANON_API_KEYS=<key>[,<key>...] to enforce."
    )


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Enforce ``FLYCANON_API_KEYS`` on ``/api/v1/*`` and the admin path.

    ``settings`` is resolved lazily on first request (via
    :func:`get_settings`) unless injected, so the ASGI app can be built
    at import time -- as :mod:`flycanon.main` does -- before the
    environment is fully read by tests that monkeypatch it.
    """

    def __init__(
        self,
        app: Any,
        *,
        settings: CanonSettings | None = None,
        admin_path: str = "/admin",
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._admin_path = admin_path

    @property
    def settings(self) -> CanonSettings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        configured = self.settings.api_key_set
        if not configured:
            return await call_next(request)
        path = request.url.path
        has_agent_token = bool(request.headers.get(HEADER_AGENT_TOKEN))
        if not path_requires_key(path, admin_path=self._admin_path, has_agent_token=has_agent_token):
            return await call_next(request)
        presented = extract_api_key(request.headers)
        if presented is None:
            return self._refuse(
                request,
                MissingApiKey("X-API-Key or 'Authorization: ApiKey <key>' is required."),
            )
        if not key_matches(presented, configured):
            return self._refuse(request, InvalidApiKey("The presented API key is not recognised."))
        setattr(request.state, PRINCIPAL_STATE_ATTR, principal_for(presented))
        return await call_next(request)

    @staticmethod
    def _refuse(request: Request, exc: FireflyHTTPException) -> Response:
        # Rendered, not raised: see the module docstring on why a
        # Starlette middleware cannot rely on the exception handlers.
        return problem_response_for(exc, request)


class ApiKeyPrincipalFilter(OncePerRequestFilter):
    """Bridge the validated key into pyfly's ``SecurityContext``.

    Registered as a bean (see ``CanonCoreConfiguration``) so pyfly's
    late filter discovery appends it to the ``WebFilterChain``. It
    orders just after ``RequestContextFilter`` (``HIGHEST_PRECEDENCE``)
    because the ``RequestContext`` it writes to does not exist before
    that filter runs. Requests without a recorded principal (open
    mode, public paths, agent-token requests) leave the context
    untouched, so pyfly's admin gate keeps refusing them when
    ``require-auth`` is on.
    """

    __pyfly_order__ = HIGHEST_PRECEDENCE + 100

    async def do_filter(self, request: Any, call_next: CallNext) -> Any:
        principal = getattr(getattr(request, "state", None), PRINCIPAL_STATE_ATTR, None)
        if principal:
            from pyfly.context.request_context import RequestContext
            from pyfly.security.context import SecurityContext

            rc = RequestContext.current()
            if rc is not None:
                rc.security_context = SecurityContext(user_id=principal, roles=[API_KEY_ROLE])
        return await call_next(request)


__all__ = [
    "AGENT_PREFIX",
    "API_KEY_ROLE",
    "API_PREFIX",
    "PRINCIPAL_STATE_ATTR",
    "PUBLIC_PATHS",
    "ApiKeyMiddleware",
    "ApiKeyPrincipalFilter",
    "extract_api_key",
    "key_matches",
    "log_api_key_mode",
    "path_requires_key",
    "principal_for",
]
