# Copyright 2026 Firefly Software Solutions Inc
"""``validate_slug`` covers the tenant_id / workspace_id charset.

The spec restricts both to ``^[a-z0-9][a-z0-9_-]{0,63}$``: 1-64
characters, must start alphanumeric, lowercase ASCII + digits +
``_`` + ``-``. This shape keeps slugs URL-safe, filesystem-safe,
and S3-key-safe.
"""

from __future__ import annotations

import pytest

from flycanon.web.conventions.validation import (
    InvalidSlugError,
    validate_slug,
)


@pytest.mark.parametrize(
    "slug",
    [
        "acme",
        "ws-q3",
        "ws_q3",
        "acme-2026-q3",
        "a",
        "0",
        "a" * 64,
        "1abc",
        "tn-abc_def-123",
    ],
)
def test_validate_slug_accepts_valid(slug: str) -> None:
    assert validate_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    [
        "",  # empty
        "A",  # uppercase
        "ACME",  # uppercase
        " acme",  # leading whitespace
        "acme ",  # trailing whitespace
        "acme.q3",  # dot
        "acme/q3",  # slash
        "acme:q3",  # colon
        "_acme",  # leading underscore
        "-acme",  # leading dash
        "a" * 65,  # 65 chars
        "café",  # non-ASCII
        "acme\nq3",  # newline
    ],
)
def test_validate_slug_rejects_invalid(slug: str) -> None:
    with pytest.raises(InvalidSlugError):
        validate_slug(slug)
