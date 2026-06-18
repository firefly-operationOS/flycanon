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

"""Shared page-text cache layered over the lazy :class:`CanonDocStore`.

The store fetches each in-scope filing's original from the
:class:`ObjectStore` and extracts its page list on first access -- the
fetch + PyMuPDF extraction is the expensive part of an RLM query, and it
otherwise happens once per :class:`CanonDocStore`, i.e. once per query.
This module shares that work across queries (and, with the Redis
backend, across replicas).

The cache is keyed by the source's ``content_sha256``: identical bytes
hit the same entry no matter which workspace/key route to them, and a
re-ingested source (new bytes -> new sha) misses the stale entry
automatically, so there is no explicit invalidation path.

Two backends, both exposing the same synchronous
``get(key) -> list[str] | None`` / ``set(key, pages)`` surface (the
cache is read from the RLM engine's worker thread, so it must be
synchronous -- NOT ``redis.asyncio``):

* :class:`MemoryPageCache` -- a bounded LRU (:class:`OrderedDict`,
  capped at ``max_entries``) with a per-entry TTL. It is a process
  singleton shared across concurrent REPL worker threads, so every
  mutation is guarded by a :class:`threading.Lock`.
* :class:`RedisPageCache` -- a synchronous :class:`redis.Redis` client
  storing the page list as JSON with ``EX = ttl`` so Redis evicts
  entries natively. A single fetch on one replica warms the whole
  fleet.

:func:`build_page_cache` selects the backend from
:class:`CanonSettings`, mirroring :func:`flycanon.core.configuration._use_redis`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import redis

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)


# Clock-source injection so tests can drive TTL expiry without sleeping.
NowFn = Callable[[], float]


@runtime_checkable
class CorpusPageCache(Protocol):
    """Synchronous page-text cache: ``content_sha256 -> page list``.

    Both backends satisfy this structurally. ``get`` returns the cached
    page list or ``None`` on a miss (or an expired entry); ``set``
    stores a page list under the key.
    """

    def get(self, key: str) -> list[str] | None: ...

    def set(self, key: str, pages: list[str]) -> None: ...


@dataclass(slots=True)
class _Entry:
    """A cached page list plus the monotonic time it expires at."""

    pages: list[str]
    expires_at: float


class MemoryPageCache:
    """Thread-safe, bounded LRU page cache with per-entry TTL.

    :param ttl_s: Per-entry time-to-live in seconds.
    :param max_entries: LRU cap. When the cache is full and a new entry
        lands, the least-recently-used entry is dropped.
    :param now_fn: Clock-source override (defaults to
        :func:`time.monotonic`); tests inject a stub to drive TTL expiry
        without sleeping.

    The cache is a process singleton hit by concurrent REPL worker
    threads, so a single coarse-grained :class:`threading.Lock` guards
    every read and write of the underlying :class:`OrderedDict`.
    """

    def __init__(
        self,
        *,
        ttl_s: int,
        max_entries: int,
        now_fn: NowFn | None = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._ttl = int(ttl_s)
        self._max_entries = int(max_entries)
        self._now: NowFn = now_fn or time.monotonic
        self._cache: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> list[str] | None:
        """Return the cached pages for ``key`` or ``None`` (miss / expired)."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if self._now() >= entry.expires_at:
                # Expired -- drop it lazily; caller refetches + repopulates.
                self._cache.pop(key, None)
                return None
            # Refresh LRU order on hit.
            self._cache.move_to_end(key)
            return list(entry.pages)

    def set(self, key: str, pages: list[str]) -> None:
        """Store ``pages`` under ``key``, applying TTL + LRU eviction."""
        with self._lock:
            # Re-insert after pop so the OrderedDict order is honest.
            self._cache.pop(key, None)
            self._cache[key] = _Entry(pages=list(pages), expires_at=self._now() + self._ttl)
            while len(self._cache) > self._max_entries:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("corpus_page_cache_lru_evict key=%s", evicted_key)


class RedisPageCache:
    """Redis-backed shared page cache: page list as JSON with ``EX = ttl``.

    :param client: A synchronous :class:`redis.Redis` client.
    :param ttl_s: TTL applied to every key so Redis evicts entries
        natively -- no in-process LRU cap needed.
    :param prefix: Key prefix so flycanon shares a Redis instance with
        other Firefly services without colliding.

    The client is synchronous (NOT ``redis.asyncio``) because the cache
    is read from the RLM engine's worker thread.
    """

    def __init__(self, client: redis.Redis, *, ttl_s: int, prefix: str = "canon:pages:") -> None:
        self._client = client
        self._ttl = int(ttl_s)
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> list[str] | None:
        """Return the cached pages for ``key`` or ``None`` on a miss."""
        raw = self._client.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return list(json.loads(raw))

    def set(self, key: str, pages: list[str]) -> None:
        """Store ``pages`` as JSON under ``key`` with ``EX = ttl_s``."""
        self._client.set(self._key(key), json.dumps(pages), ex=self._ttl)


def build_page_cache(settings: CanonSettings) -> CorpusPageCache:
    """Select the corpus page-cache backend from settings.

    Resolution is identical to
    :func:`flycanon.core.configuration._use_redis` (the same logic the
    rate-limit and idempotency stores use), kept inline here only to
    avoid a circular import (``configuration`` imports this module):

    * ``FLYCANON_CORPUS_CACHE_BACKEND=redis`` -- always Redis.
    * ``FLYCANON_CORPUS_CACHE_BACKEND=in_memory`` -- always in-memory.
    * unset / ``auto`` (the default) / any unknown value -- Redis when
      ``redis_url`` is set, in-memory otherwise.

    The Redis client is synchronous: the cache is read from the RLM
    engine's worker thread, so it must not be ``redis.asyncio``.
    """
    explicit = (settings.corpus_cache_backend or "auto").lower()
    if explicit == "redis":
        use_redis = True
    elif explicit in {"in_memory", "memory"}:
        use_redis = False
    else:
        use_redis = bool(settings.redis_url)
    if use_redis:
        return RedisPageCache(
            redis.Redis.from_url(settings.redis_url),
            ttl_s=settings.corpus_cache_ttl_s,
        )
    return MemoryPageCache(
        ttl_s=settings.corpus_cache_ttl_s,
        max_entries=settings.corpus_cache_max_entries,
    )


__all__ = [
    "CorpusPageCache",
    "MemoryPageCache",
    "NowFn",
    "RedisPageCache",
    "build_page_cache",
]
