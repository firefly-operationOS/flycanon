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

"""Concrete exception classes that map 1:1 to ProblemDetail rows.

Each subclass declares its ``status``, ``code``, ``title`` as
class attributes. The handler renders any ``FireflyHTTPException``
to a ``ProblemDetail`` JSON response without per-class mapper code.

Adding a new error type = adding a new subclass here + (optionally)
documenting it in the OpenAPI spec.
"""

from __future__ import annotations

from typing import Any, ClassVar

_BASE_URI = "https://firefly.dev/problems"


class FireflyHTTPException(Exception):
    """Base class. Concrete subclasses set ``status``, ``code``, ``title``."""

    status: ClassVar[int] = 0
    code: ClassVar[str] = ""
    title: ClassVar[str] = ""

    def __init__(
        self,
        detail: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        instance: str | None = None,
    ) -> None:
        # Subclasses must override these three class attrs. The
        # base raises so a typo (e.g. missing ``code``) surfaces at
        # construction time instead of at the JSON-render step.
        assert self.status > 0, f"{type(self).__name__}.status must be set"
        assert self.code, f"{type(self).__name__}.code must be set"
        assert self.title, f"{type(self).__name__}.title must be set"
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or []
        self.instance = instance

    @property
    def type_uri(self) -> str:
        return f"{_BASE_URI}/{self.code}"


# -- 400 -------------------------------------------------------------


class MissingIdempotencyKey(FireflyHTTPException):
    status = 400
    code = "missing_idempotency_key"
    title = "Missing Idempotency-Key header"


class MissingTenantContext(FireflyHTTPException):
    status = 400
    code = "missing_tenant_context"
    title = "Missing tenant context"


class InvalidRequest(FireflyHTTPException):
    status = 422
    code = "invalid_request"
    title = "Invalid request"


# -- 401 -------------------------------------------------------------


class MissingApiKey(FireflyHTTPException):
    """``FLYCANON_API_KEYS`` is configured and the request carried no key.

    Raised (rendered, in fact -- see :class:`ApiKeyMiddleware`) for
    every ``/api/v1/*`` and ``/admin/*`` request that presents neither
    ``X-API-Key`` nor ``Authorization: ApiKey <key>`` while the
    service runs in keyed mode. Agent-tier routes that carry
    ``X-Agent-Token`` are exempt: the token is their credential.
    """

    status = 401
    code = "missing_api_key"
    title = "Missing API key"


class InvalidApiKey(FireflyHTTPException):
    """A key was presented but matches none of ``FLYCANON_API_KEYS``."""

    status = 401
    code = "invalid_api_key"
    title = "Invalid API key"


class CallbackUrlNotAllowed(FireflyHTTPException):
    """``callback_url`` points at a private, loopback or link-local host.

    The async-ingest webhook is an outbound POST the service makes on
    the caller's behalf; letting it target ``127.0.0.1``, RFC 1918
    space or the cloud metadata endpoint would turn the ingest API
    into a server-side request forgery primitive. The same host policy
    that guards ``uri`` fetches (:class:`UrlFetcher`) rejects it at
    submit time so the job is never queued.
    """

    status = 400
    code = "callback_url_not_allowed"
    title = "Callback URL not allowed"


class WorkspaceScopeMismatch(FireflyHTTPException):
    """The path ``{workspace_id}`` and ``X-Workspace-Id`` disagree.

    Raised by the destructive workspace verbs (``:purge``). Under the
    production RLS role every row visible to the request is already
    filtered by the header workspace, so a purge addressed to a
    different path id would silently touch nothing -- or, under a
    BYPASSRLS dev role, touch the wrong workspace. Refusing the
    ambiguity up front keeps the verb deterministic on both roles.
    """

    status = 400
    code = "workspace_scope_mismatch"
    title = "Workspace scope mismatch"


# -- 402 -------------------------------------------------------------


class BudgetExceeded(FireflyHTTPException):
    status = 402
    code = "budget_exceeded"
    title = "Budget exceeded"


# -- 403 -------------------------------------------------------------


class TenantClaimMismatch(FireflyHTTPException):
    status = 403
    code = "tenant_claim_mismatch"
    title = "Tenant claim mismatch"


# -- 404 -------------------------------------------------------------


class ResourceNotFound(FireflyHTTPException):
    status = 404
    code = "resource_not_found"
    title = "Resource not found"


class WorkspaceNotFound(FireflyHTTPException):
    status = 404
    code = "workspace_not_found"
    title = "Workspace not found"


class SourceNotFound(FireflyHTTPException):
    """Rendered form of the service-layer ``SourceNotFound``.

    The intake service raises its own (plain-``Exception``)
    ``SourceNotFound``; the conventions handler bridges it to this
    class so the wire contract is ``404 source_not_found`` as the
    source endpoints document.
    """

    status = 404
    code = "source_not_found"
    title = "Source not found"


# -- 409 -------------------------------------------------------------


class IdempotencyKeyConflict(FireflyHTTPException):
    status = 409
    code = "idempotency_key_conflict"
    title = "Idempotency-Key conflict"


# -- URL fetch (400 / 413 / 502) ---------------------------------------
#
# ``UrlFetcher`` raises a plain ``UrlFetchError`` carrying one of these
# codes; the conventions handler bridges it to the matching class so a
# refused ``uri`` renders as the documented problem+json instead of the
# framework's generic 500. Client-side mistakes (scheme, forbidden host)
# are 400s, an oversize origin is 413, and anything the ORIGIN did wrong
# (4xx/5xx, network failure, redirect loop) is 502 Bad Gateway -- the
# caller's request was fine, the upstream was not.


class UrlFetchForbiddenHost(FireflyHTTPException):
    status = 400
    code = "url_fetch_forbidden_host"
    title = "URL host not allowed"


class UrlFetchUnsupportedScheme(FireflyHTTPException):
    status = 400
    code = "url_fetch_unsupported_scheme"
    title = "URL scheme not supported"


class UrlFetchTooLarge(FireflyHTTPException):
    status = 413
    code = "url_fetch_too_large"
    title = "Fetched document too large"


class UrlFetchUpstreamError(FireflyHTTPException):
    status = 502
    code = "url_fetch_http_error"
    title = "Origin returned an error"


class UrlFetchFailed(FireflyHTTPException):
    status = 502
    code = "url_fetch_failed"
    title = "Origin could not be fetched"


class UrlFetchTooManyRedirects(FireflyHTTPException):
    status = 502
    code = "url_fetch_too_many_redirects"
    title = "Origin redirected too many times"


#: ``UrlFetchError.code`` -> rendered class. Unknown codes fall back to
#: :class:`UrlFetchFailed` so a future fetcher code still renders as 502.
URL_FETCH_EXCEPTIONS: dict[str, type[FireflyHTTPException]] = {
    UrlFetchForbiddenHost.code: UrlFetchForbiddenHost,
    UrlFetchUnsupportedScheme.code: UrlFetchUnsupportedScheme,
    UrlFetchTooLarge.code: UrlFetchTooLarge,
    UrlFetchUpstreamError.code: UrlFetchUpstreamError,
    UrlFetchFailed.code: UrlFetchFailed,
    UrlFetchTooManyRedirects.code: UrlFetchTooManyRedirects,
}


# -- 500 -------------------------------------------------------------


class CommandProcessingError(FireflyHTTPException):
    """Fallback for pyfly ``CommandProcessingException`` causes we don't
    know how to map.

    The conventions handler unwraps the wrapped ``cause`` and renders
    its concrete type (``FireflyHTTPException`` or
    ``ResourceNotFoundException``) directly when possible. When the
    cause is something we don't recognise (e.g. a raw ``ValueError``)
    we fall back to this 500 so the caller still gets the
    ``ProblemDetail`` envelope instead of pyfly's legacy ``{error: ...}``
    shape.
    """

    status = 500
    code = "command_processing_error"
    title = "Command processing error"
