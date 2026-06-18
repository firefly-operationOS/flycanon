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

"""Unit tests for the corpus page cache (memory LRU + redis backends).

TTL expiry is driven by an injected clock rather than sleeping. The Redis
backend is exercised against a fake synchronous redis client so no real
Redis (and no network) is needed.
"""

from __future__ import annotations

import json

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.query.rlm.page_cache import (
    CorpusPageCache,
    MemoryPageCache,
    RedisPageCache,
    build_page_cache,
)


class FakeClock:
    """A monotonic clock stub: ``advance`` moves time forward in tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeSyncRedis:
    """In-memory stand-in for a synchronous ``redis.Redis`` client.

    Stores raw string values keyed by the full prefixed key. ``ex`` is
    recorded per key so a test can assert the TTL was passed through.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ex: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ex[key] = ex


# -- MemoryPageCache -----------------------------------------------------


def test_memory_cache_get_miss_then_hit():
    cache = MemoryPageCache(ttl_s=100, max_entries=8)
    assert cache.get("sha-a") is None
    cache.set("sha-a", ["p1", "p2"])
    assert cache.get("sha-a") == ["p1", "p2"]


def test_memory_cache_get_returns_a_copy():
    """A mutated returned list must not corrupt the cached entry."""
    cache = MemoryPageCache(ttl_s=100, max_entries=8)
    cache.set("sha-a", ["p1"])
    got = cache.get("sha-a")
    got.append("mutated")
    assert cache.get("sha-a") == ["p1"]


def test_memory_cache_lru_evicts_at_max_entries():
    cache = MemoryPageCache(ttl_s=100, max_entries=2)
    cache.set("a", ["1"])
    cache.set("b", ["2"])
    cache.set("c", ["3"])  # evicts the LRU ("a")
    assert cache.get("a") is None
    assert cache.get("b") == ["2"]
    assert cache.get("c") == ["3"]


def test_memory_cache_get_refreshes_lru_order():
    cache = MemoryPageCache(ttl_s=100, max_entries=2)
    cache.set("a", ["1"])
    cache.set("b", ["2"])
    # Touch "a" so "b" becomes the LRU.
    assert cache.get("a") == ["1"]
    cache.set("c", ["3"])  # evicts "b", not the just-touched "a"
    assert cache.get("a") == ["1"]
    assert cache.get("b") is None
    assert cache.get("c") == ["3"]


def test_memory_cache_ttl_expiry_forces_refetch():
    clock = FakeClock()
    cache = MemoryPageCache(ttl_s=10, max_entries=8, now_fn=clock)
    cache.set("a", ["1"])
    clock.advance(9)
    assert cache.get("a") == ["1"]  # still fresh
    clock.advance(1)  # now == expires_at -> expired
    assert cache.get("a") is None


def test_memory_cache_rejects_nonpositive_max_entries():
    with pytest.raises(ValueError):
        MemoryPageCache(ttl_s=10, max_entries=0)


def test_memory_cache_satisfies_protocol():
    cache = MemoryPageCache(ttl_s=10, max_entries=8)
    assert isinstance(cache, CorpusPageCache)


# -- RedisPageCache ------------------------------------------------------


def test_redis_cache_set_stores_json_with_ttl():
    client = FakeSyncRedis()
    cache = RedisPageCache(client, ttl_s=42)
    cache.set("sha-a", ["p1", "p2"])
    assert client.store["canon:pages:sha-a"] == json.dumps(["p1", "p2"])
    assert client.ex["canon:pages:sha-a"] == 42


def test_redis_cache_round_trips():
    cache = RedisPageCache(FakeSyncRedis(), ttl_s=42)
    cache.set("sha-a", ["p1", "p2"])
    assert cache.get("sha-a") == ["p1", "p2"]


def test_redis_cache_miss_returns_none():
    cache = RedisPageCache(FakeSyncRedis(), ttl_s=42)
    assert cache.get("absent") is None


def test_redis_cache_decodes_bytes():
    client = FakeSyncRedis()
    client.store["canon:pages:sha-a"] = json.dumps(["x"]).encode("utf-8")
    cache = RedisPageCache(client, ttl_s=42)
    assert cache.get("sha-a") == ["x"]


def test_redis_cache_satisfies_protocol():
    assert isinstance(RedisPageCache(FakeSyncRedis(), ttl_s=10), CorpusPageCache)


# -- build_page_cache factory --------------------------------------------


def test_build_page_cache_auto_no_redis_url_is_memory():
    settings = CanonSettings(corpus_cache_backend="auto", redis_url="")
    cache = build_page_cache(settings)
    assert isinstance(cache, MemoryPageCache)


def test_build_page_cache_memory_forced_even_with_redis_url():
    settings = CanonSettings(corpus_cache_backend="memory", redis_url="redis://localhost:6379/0")
    assert isinstance(build_page_cache(settings), MemoryPageCache)


def test_build_page_cache_auto_with_redis_url_is_redis():
    settings = CanonSettings(corpus_cache_backend="auto", redis_url="redis://localhost:6379/0")
    assert isinstance(build_page_cache(settings), RedisPageCache)


def test_build_page_cache_redis_forced():
    settings = CanonSettings(corpus_cache_backend="redis", redis_url="redis://localhost:6379/0")
    assert isinstance(build_page_cache(settings), RedisPageCache)


def test_build_page_cache_honours_memory_knobs():
    settings = CanonSettings(
        corpus_cache_backend="memory",
        corpus_cache_ttl_s=7,
        corpus_cache_max_entries=3,
    )
    cache = build_page_cache(settings)
    assert isinstance(cache, MemoryPageCache)
    cache.set("a", ["1"])
    cache.set("b", ["2"])
    cache.set("c", ["3"])
    cache.set("d", ["4"])  # cap is 3 -> "a" evicted
    assert cache.get("a") is None
    assert cache.get("d") == ["4"]
