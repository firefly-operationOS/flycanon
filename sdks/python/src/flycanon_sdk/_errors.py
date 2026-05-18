# Copyright 2026 Firefly Software Solutions Inc
"""SDK exception hierarchy.

All errors that originate from the wire carry the service's stable
``code`` field; callers branch on ``code`` instead of parsing the
human-readable ``detail``.
"""

from __future__ import annotations

from typing import Any


class CanonError(Exception):
    """Base class for every flycanon-sdk error."""


class CanonConnectionError(CanonError):
    """Raised when the SDK could not reach the service (network,
    timeout, TLS, ...)."""


class CanonAPIError(CanonError):
    """Raised on any non-2xx response.

    Carries the stable ``code`` from the service's ProblemDetails
    payload plus the raw response body.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        title: str,
        detail: str | None,
        extensions: dict[str, Any] | None,
        payload: dict[str, Any] | str,
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
