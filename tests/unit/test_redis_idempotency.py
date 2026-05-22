# Copyright 2026 Firefly Software Solutions Inc
"""``RedisIdempotencyStore`` -- Redis-backed replay-dedup store.

The adapter is covered without a real Redis instance by mocking
``redis.asyncio.Redis`` -- the SET+EXPIRE / GET behaviour is verified
by simulating Redis's own semantics inside a dict-backed mock. The
docker-compose stack covers the surface end-to-end against a real
Redis; these unit tests pin the wire shape (key layout, JSON
encoding, TTL value) the adapter writes.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from flycanon.web.conventions.idempotency import DEFAULT_IDEMPOTENCY_TTL
from flycanon.web.conventions.redis_idempotency import RedisIdempotencyStore

# ---------------------------------------------------------------------
# Redis-shaped mock backed by an in-memory dict.
# ---------------------------------------------------------------------


class _RedisStub:
    """Minimal Redis surface used by :class:`RedisIdempotencyStore`."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
def redis_stub() -> _RedisStub:
    return _RedisStub()


@pytest.fixture
def store(redis_stub: _RedisStub) -> RedisIdempotencyStore:
    return RedisIdempotencyStore(redis_stub)  # type: ignore[arg-type]


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_returns_none_when_absent(
    store: RedisIdempotencyStore,
) -> None:
    """An unknown key resolves to ``None`` so the caller dispatches."""
    result = await store.lookup(tenant_id="acme", route="agent.sources:ingest", key="K1")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_returns_none_for_empty_key(
    store: RedisIdempotencyStore,
) -> None:
    """Defensive: an empty key never round-trips to Redis."""
    result = await store.lookup(tenant_id="acme", route="agent.sources:ingest", key="")
    assert result is None


@pytest.mark.asyncio
async def test_record_response_persists_response_under_namespaced_key(
    store: RedisIdempotencyStore,
    redis_stub: _RedisStub,
) -> None:
    """The Redis key layout encodes (tenant, route, key) verbatim."""
    await store.record_response(
        tenant_id="acme",
        route="agent.sources:ingest",
        key="K1",
        status=201,
        json_body={"id": "src-1"},
    )
    expected_key = "idemp:acme:agent.sources:ingest:K1"
    assert expected_key in redis_stub.store
    payload = json.loads(redis_stub.store[expected_key])
    assert payload == {"status": 201, "body": {"id": "src-1"}}


@pytest.mark.asyncio
async def test_record_response_sets_ttl(
    store: RedisIdempotencyStore,
    redis_stub: _RedisStub,
) -> None:
    """The TTL persisted to Redis matches ``DEFAULT_IDEMPOTENCY_TTL``."""
    await store.record_response(
        tenant_id="acme",
        route="agent.sources:ingest",
        key="K1",
        status=201,
        json_body={"id": "src-1"},
    )
    expected_key = "idemp:acme:agent.sources:ingest:K1"
    assert redis_stub.expirations[expected_key] == int(DEFAULT_IDEMPOTENCY_TTL.total_seconds())


@pytest.mark.asyncio
async def test_record_response_empty_key_noop(
    store: RedisIdempotencyStore,
    redis_stub: _RedisStub,
) -> None:
    """An empty key is a no-op -- the helper short-circuits before Redis."""
    await store.record_response(
        tenant_id="acme",
        route="agent.sources:ingest",
        key="",
        status=201,
        json_body={"id": "src-1"},
    )
    assert redis_stub.store == {}


@pytest.mark.asyncio
async def test_lookup_round_trip_after_record(
    store: RedisIdempotencyStore,
) -> None:
    """``record_response`` then ``lookup`` returns the same envelope."""
    await store.record_response(
        tenant_id="acme",
        route="agent.sources:ingest",
        key="K1",
        status=201,
        json_body={"id": "src-1", "n": 3},
    )
    result = await store.lookup(
        tenant_id="acme", route="agent.sources:ingest", key="K1"
    )
    assert result is not None
    assert result.status == 201
    assert result.body == {"id": "src-1", "n": 3}


@pytest.mark.asyncio
async def test_lookup_handles_bytes_response_from_redis() -> None:
    """``redis.get`` may return ``bytes`` when ``decode_responses=False``.

    The adapter must decode UTF-8 so the JSON parser accepts the
    payload either way -- the configuration helper deliberately
    leaves decoding off so callers can choose.
    """
    client = MagicMock()
    payload = json.dumps({"status": 200, "body": {"hits": []}}).encode("utf-8")
    client.get = AsyncMock(return_value=payload)
    store = RedisIdempotencyStore(client)
    result = await store.lookup(tenant_id="acme", route="agent.search:run", key="K1")
    assert result is not None
    assert result.status == 200
    assert result.body == {"hits": []}


@pytest.mark.asyncio
async def test_distinct_tenants_do_not_collide(
    store: RedisIdempotencyStore,
) -> None:
    """Same (route, key) but distinct tenants resolve to distinct slots."""
    await store.record_response(
        tenant_id="acme",
        route="agent.sources:ingest",
        key="K",
        status=201,
        json_body={"who": "acme"},
    )
    await store.record_response(
        tenant_id="bcorp",
        route="agent.sources:ingest",
        key="K",
        status=201,
        json_body={"who": "bcorp"},
    )
    acme = await store.lookup(tenant_id="acme", route="agent.sources:ingest", key="K")
    bcorp = await store.lookup(tenant_id="bcorp", route="agent.sources:ingest", key="K")
    assert acme is not None and acme.body == {"who": "acme"}
    assert bcorp is not None and bcorp.body == {"who": "bcorp"}


@pytest.mark.asyncio
async def test_legacy_get_returns_none(
    store: RedisIdempotencyStore,
) -> None:
    """The legacy ``get``/``put`` index is not backed by Redis -- always ``None``."""
    from flycanon.web.conventions.idempotency import IdempotencyKey

    result = store.get("acme", "ws", "/api/v1/x", IdempotencyKey("k"))
    assert result is None
