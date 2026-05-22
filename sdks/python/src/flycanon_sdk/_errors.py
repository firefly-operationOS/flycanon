# Copyright 2026 Firefly Software Solutions Inc
"""SDK exception hierarchy.

All errors that originate from the wire carry the service's stable
``code`` field; callers branch on ``code`` instead of parsing the
human-readable ``detail``.

The 26.6.0 unification adds typed subclasses keyed by the service's
RFC 7807 ``code`` field so callers can ``except InvalidAgentToken``
instead of ``except CanonAPIError if err.code == "invalid_agent_token"``.
A small registry (:data:`_CODE_REGISTRY`) maps each known code to its
subclass; :func:`_resolve_api_error_class` falls back to
:class:`CanonAPIError` for unrecognised codes so older clients
talking to newer services keep working.

:class:`ValidationError` is the one shape that carries structured
per-field issues: the service ships an ``errors`` array of
``{code, path, message}`` triples (see ``ProblemDetail.errors``);
we parse those into :class:`FieldError` instances and expose them
on :attr:`ValidationError.errors`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CanonError(Exception):
    """Base class for every flycanon-sdk error."""


class CanonConnectionError(CanonError):
    """Raised when the SDK could not reach the service (network,
    timeout, TLS, ...)."""


class CanonAPIError(CanonError):
    """Raised on any non-2xx response.

    Carries the stable ``code`` from the service's ProblemDetails
    payload plus the raw response body. Subclasses (e.g.
    :class:`InvalidAgentToken`, :class:`MissingIdempotencyKey`,
    :class:`ValidationError`) refine the type for the common
    machine-readable codes -- catch the subclass to handle a
    specific failure mode, catch :class:`CanonAPIError` to handle
    every API error generically.
    """

    #: Subclasses set this so :func:`_raise_for_problem` can route
    #: by ``code``. Subclasses that do not override (the base class)
    #: are matched on every unknown code.
    code_match: str | None = None

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        title: str,
        detail: str | None,
        extensions: dict[str, Any] | None,
        payload: dict[str, Any] | str,
        errors: list[FieldError] | None = None,
    ) -> None:
        message = f"{status_code} {code}: {title}"
        if detail:
            message += f" -- {detail}"
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.title = title
        self.detail = detail
        self.extensions = dict(extensions or {})
        self.payload = payload
        self.errors: list[FieldError] = list(errors or [])


@dataclass(frozen=True)
class FieldError:
    """One entry from the ``ProblemDetail.errors`` per-field list.

    Mirrors the service's ``{code, path, message}`` triple. Used as
    the structured per-field payload on :class:`ValidationError`
    (and on any other typed error that carries an ``errors`` list).
    """

    code: str
    path: str
    message: str


# ----------------------------------------------------------------------
# Code-keyed subclasses
# ----------------------------------------------------------------------


class ValidationError(CanonAPIError):
    """``400 invalid_request`` -- per-field validation errors.

    The :attr:`errors` list carries one :class:`FieldError` per
    failing field, parsed from ``ProblemDetail.errors``. Callers can
    branch on ``err.path`` / ``err.code`` to surface field-level
    messages.
    """

    code_match = "invalid_request"


class MissingIdempotencyKey(CanonAPIError):
    """``400 missing_idempotency_key`` -- agent-tier POST without
    the ``Idempotency-Key`` header."""

    code_match = "missing_idempotency_key"


class MissingAgentToken(CanonAPIError):
    """``401 missing_agent_token`` -- ``X-Agent-Token`` is required
    for ``/api/v1/agent/*`` routes but was not sent."""

    code_match = "missing_agent_token"


class InvalidAgentToken(CanonAPIError):
    """``403 invalid_agent_token`` -- the token shape is malformed,
    its prefix is unknown, or the stored hash does not match."""

    code_match = "invalid_agent_token"


class AgentTokenExpired(CanonAPIError):
    """``403 agent_token_expired`` -- the token's ``expires_at`` is
    in the past."""

    code_match = "agent_token_expired"


class AgentWorkspaceNotInAllowlist(CanonAPIError):
    """``403 agent_workspace_not_in_allowlist`` -- the token's
    allowlist is non-empty and does not include the
    ``X-Workspace-Id`` header."""

    code_match = "agent_workspace_not_in_allowlist"


class AgentScopeDenied(CanonAPIError):
    """``403 agent_scope_denied`` -- the token's scopes do not
    include the per-route scope (and do not include ``"*"``)."""

    code_match = "agent_scope_denied"


class AgentCannotMint(CanonAPIError):
    """``403 agent_cannot_mint`` -- an agent-tier caller tried to
    use the user-tier ``/api/v1/agent-tokens`` CRUD surface."""

    code_match = "agent_cannot_mint"


_CODE_REGISTRY: dict[str, type[CanonAPIError]] = {
    cls.code_match: cls
    for cls in (
        ValidationError,
        MissingIdempotencyKey,
        MissingAgentToken,
        InvalidAgentToken,
        AgentTokenExpired,
        AgentWorkspaceNotInAllowlist,
        AgentScopeDenied,
        AgentCannotMint,
    )
    if cls.code_match is not None
}


def _resolve_api_error_class(code: str) -> type[CanonAPIError]:
    """Return the subclass that matches ``code`` or
    :class:`CanonAPIError` for unknown codes."""
    return _CODE_REGISTRY.get(code, CanonAPIError)


def _parse_field_errors(raw: Any) -> list[FieldError]:
    """Parse ``ProblemDetail.errors`` into typed :class:`FieldError`.

    The service emits an ``errors`` array of ``{code, path, message}``
    dicts. We tolerate missing keys (defaulting to empty strings) so
    consumers can still inspect the structured list when the service
    drifts.
    """
    if not isinstance(raw, list):
        return []
    parsed: list[FieldError] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        parsed.append(
            FieldError(
                code=str(entry.get("code") or ""),
                path=str(entry.get("path") or ""),
                message=str(entry.get("message") or ""),
            )
        )
    return parsed


__all__ = [
    "AgentCannotMint",
    "AgentScopeDenied",
    "AgentTokenExpired",
    "AgentWorkspaceNotInAllowlist",
    "CanonAPIError",
    "CanonConnectionError",
    "CanonError",
    "FieldError",
    "InvalidAgentToken",
    "MissingAgentToken",
    "MissingIdempotencyKey",
    "ValidationError",
]
