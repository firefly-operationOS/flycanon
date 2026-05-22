# Copyright 2026 Firefly Software Solutions Inc
"""Idempotency primitives used by the agent + user tiers.

The store is a Protocol so production swaps the in-memory
implementation for a Postgres-backed one in a later plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class InvalidIdempotencyKeyError(ValueError):
    """Raised when the Idempotency-Key header value fails the charset."""


@dataclass(frozen=True)
class IdempotencyKey:
    """1-128 chars of [A-Za-z0-9_-]."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _KEY_RE.fullmatch(self.value):
            raise InvalidIdempotencyKeyError(f"invalid Idempotency-Key: {self.value!r}")


@dataclass(frozen=True)
class IdempotencyEntry:
    """One row in the idempotency store."""

    tenant_id: str
    workspace_id: str
    route: str
    key: IdempotencyKey
    response_hash: str
    job_id: str | None
    expires_at: datetime


class IdempotencyStore(Protocol):
    """Pluggable backing store. Production uses Postgres; tests use memory."""

    def get(
        self,
        tenant_id: str,
        workspace_id: str,
        route: str,
        key: IdempotencyKey,
    ) -> IdempotencyEntry | None: ...

    def put(self, entry: IdempotencyEntry) -> None: ...


@dataclass
class InMemoryIdempotencyStore(IdempotencyStore):
    """Test-only store. Expired entries are skipped on read."""

    _entries: dict[tuple[str, str, str, str], IdempotencyEntry] = field(default_factory=dict)

    def get(
        self,
        tenant_id: str,
        workspace_id: str,
        route: str,
        key: IdempotencyKey,
    ) -> IdempotencyEntry | None:
        entry = self._entries.get((tenant_id, workspace_id, route, key.value))
        if entry is None:
            return None
        if entry.expires_at <= datetime.now(UTC):
            return None
        return entry

    def put(self, entry: IdempotencyEntry) -> None:
        self._entries[(entry.tenant_id, entry.workspace_id, entry.route, entry.key.value)] = entry
