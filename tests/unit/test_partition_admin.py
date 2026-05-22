# Copyright 2026 Firefly Software Solutions Inc
"""Tier-B partition admin tool unit tests.

The pure-Python helpers (slug validation + partition name
derivation) run on every machine; the Postgres-side functions
require a live database and are not exercised here (a future
testcontainer test under tests/integration/ can cover them
end-to-end if needed).
"""

from __future__ import annotations

import pytest

from flycanon.core.services.retrieval.partition_admin import _partition_name
from flycanon.web.conventions.validation import InvalidSlugError


def test_partition_name_uses_canon_chunk_vectors_prefix() -> None:
    assert _partition_name("acme") == "canon_chunk_vectors_acme"


def test_partition_name_accepts_slug_dashes_and_underscores() -> None:
    assert _partition_name("acme-corp_123") == "canon_chunk_vectors_acme-corp_123"


@pytest.mark.parametrize(
    "bad",
    [
        "ACME",
        "acme!",
        "acme; DROP TABLE x",
        "",
        " acme",
    ],
)
def test_partition_name_rejects_invalid_slugs(bad: str) -> None:
    with pytest.raises(InvalidSlugError):
        _partition_name(bad)
