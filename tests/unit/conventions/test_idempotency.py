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

"""``IdempotencyKey`` value object + ``IdempotencyStore`` protocol.

The agent-tier requires idempotency-key on every POST; user-tier
accepts it optionally. The store is pluggable: in-memory for
tests, Postgres-backed in production (added in a later plan).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flycanon.web.conventions.idempotency import (
    IdempotencyEntry,
    IdempotencyKey,
    InMemoryIdempotencyStore,
    InvalidIdempotencyKeyError,
)


def test_idempotency_key_accepts_valid() -> None:
    k = IdempotencyKey("abc-123_DEF")
    assert k.value == "abc-123_DEF"


def test_idempotency_key_rejects_empty() -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        IdempotencyKey("")


def test_idempotency_key_rejects_too_long() -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        IdempotencyKey("x" * 129)


def test_idempotency_key_rejects_whitespace() -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        IdempotencyKey("abc 123")


def test_in_memory_store_get_returns_none_for_unknown_key() -> None:
    store = InMemoryIdempotencyStore()
    result = store.get("acme", "ws", "/api/v1/x", IdempotencyKey("k1"))
    assert result is None


def test_in_memory_store_put_then_get() -> None:
    store = InMemoryIdempotencyStore()
    entry = IdempotencyEntry(
        tenant_id="acme",
        workspace_id="ws",
        route="/api/v1/x",
        key=IdempotencyKey("k1"),
        response_hash="hash-1",
        job_id="job-1",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    store.put(entry)
    fetched = store.get("acme", "ws", "/api/v1/x", IdempotencyKey("k1"))
    assert fetched == entry


def test_in_memory_store_expired_entries_treated_as_absent() -> None:
    store = InMemoryIdempotencyStore()
    expired = IdempotencyEntry(
        tenant_id="acme",
        workspace_id="ws",
        route="/api/v1/x",
        key=IdempotencyKey("k1"),
        response_hash="hash-1",
        job_id="job-1",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    store.put(expired)
    fetched = store.get("acme", "ws", "/api/v1/x", IdempotencyKey("k1"))
    assert fetched is None


def test_in_memory_store_scope_isolation() -> None:
    """Same key, different (tenant, workspace, route) -> distinct entries."""
    store = InMemoryIdempotencyStore()
    entry_a = IdempotencyEntry(
        tenant_id="acme",
        workspace_id="ws",
        route="/api/v1/x",
        key=IdempotencyKey("k"),
        response_hash="A",
        job_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    entry_b = IdempotencyEntry(
        tenant_id="bcorp",
        workspace_id="ws",
        route="/api/v1/x",
        key=IdempotencyKey("k"),
        response_hash="B",
        job_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    store.put(entry_a)
    store.put(entry_b)
    assert store.get("acme", "ws", "/api/v1/x", IdempotencyKey("k")) == entry_a
    assert store.get("bcorp", "ws", "/api/v1/x", IdempotencyKey("k")) == entry_b
