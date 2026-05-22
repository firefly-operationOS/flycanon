# Copyright 2026 Firefly Software Solutions Inc
"""``RedisRateLimiter`` -- Lua-backed sliding-window counter.

The adapter is covered without a real Redis instance by mocking
``redis.asyncio.Redis`` -- the Lua script's per-key behaviour is
verified by simulating Redis's own semantics inside the mock
(ZREMRANGEBYSCORE + ZCARD + conditional ZADD). Spinning a Redis
container per test is intentionally avoided here; the
docker-compose stack covers that surface end-to-end.

The mock returns ``0`` / ``1`` from ``evalsha`` to match the Lua
script's return contract; the rate-limit decision is the boolean
cast of that.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from flycanon.core.services.auth.redis_rate_limiter import RedisRateLimiter

# ---------------------------------------------------------------------
# Sliding-window simulator -- a tiny in-memory model of the Lua script
# so the mock returns the right answer (0 / 1) for each call without
# depending on a real Redis.
# ---------------------------------------------------------------------


class _SlidingWindowSimulator:
    """Mirror the ZREMRANGEBYSCORE + ZCARD + ZADD sequence in pure Python."""

    def __init__(self) -> None:
        self.windows: dict[str, list[float]] = {}

    def evalsha(self, _sha: str, _numkeys: int, key: str, now: float, limit: int) -> int:
        cutoff = float(now) - 60.0
        window = self.windows.setdefault(key, [])
        window[:] = [ts for ts in window if ts >= cutoff]
        if len(window) >= int(limit):
            return 0
        window.append(float(now))
        return 1

    def delete(self, key: str) -> int:
        self.windows.pop(key, None)
        return 1


@pytest.fixture
def simulator() -> _SlidingWindowSimulator:
    return _SlidingWindowSimulator()


@pytest.fixture
def client(simulator: _SlidingWindowSimulator) -> MagicMock:
    """Async-Redis-shaped mock backed by the in-memory simulator.

    ``script_load`` returns a sentinel sha; ``evalsha`` and ``delete``
    delegate to the simulator above so the rate-limit decision matches
    what a real Redis would return.
    """
    client = MagicMock()
    client.script_load = AsyncMock(return_value="DEADBEEF")

    async def _evalsha(sha, numkeys, key, now, limit):
        return simulator.evalsha(sha, numkeys, key, now, limit)

    async def _delete(key):
        return simulator.delete(key)

    client.evalsha = AsyncMock(side_effect=_evalsha)
    client.delete = AsyncMock(side_effect=_delete)
    return client


@pytest.fixture
def limiter(client: MagicMock) -> RedisRateLimiter:
    return RedisRateLimiter(client)


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_n_requests_within_window_admitted(
    limiter: RedisRateLimiter,
) -> None:
    """N requests within the 60s window must all succeed when limit=N."""
    for _ in range(3):
        allowed = await limiter.consume(token_id="tok-A", limit_per_minute=3, now=1000.0)
        assert allowed is True


@pytest.mark.asyncio
async def test_request_n_plus_one_rejected(
    limiter: RedisRateLimiter,
) -> None:
    """The (N+1)th request inside the same window must be rejected."""
    for _ in range(2):
        await limiter.consume(token_id="tok-B", limit_per_minute=2, now=1000.0)
    assert await limiter.consume(token_id="tok-B", limit_per_minute=2, now=1000.0) is False


@pytest.mark.asyncio
async def test_different_tokens_are_independent(
    limiter: RedisRateLimiter,
) -> None:
    """Two tokens at the cap don't interfere with a third token's budget."""
    await limiter.consume(token_id="A", limit_per_minute=1, now=1000.0)
    await limiter.consume(token_id="B", limit_per_minute=1, now=1000.0)
    # Both A and B are at their cap.
    assert await limiter.consume(token_id="A", limit_per_minute=1, now=1000.0) is False
    assert await limiter.consume(token_id="B", limit_per_minute=1, now=1000.0) is False
    # A fresh token C is unaffected.
    assert await limiter.consume(token_id="C", limit_per_minute=1, now=1000.0) is True


@pytest.mark.asyncio
async def test_window_slides_after_60s(
    limiter: RedisRateLimiter,
) -> None:
    """Older entries age out so the bucket admits a fresh request after 60s."""
    assert await limiter.consume(token_id="slide", limit_per_minute=1, now=1000.0) is True
    # Same window -- denied.
    assert await limiter.consume(token_id="slide", limit_per_minute=1, now=1000.5) is False
    # 61s later -- the old entry is past the cutoff and the new one fits.
    assert await limiter.consume(token_id="slide", limit_per_minute=1, now=1061.0) is True


@pytest.mark.asyncio
async def test_no_limit_short_circuits(
    limiter: RedisRateLimiter,
    client: MagicMock,
) -> None:
    """``limit_per_minute is None`` skips the script call entirely.

    Pins the contract that legacy tokens (no rate limit) keep working
    without hitting Redis -- saves a round-trip per request.
    """
    assert await limiter.consume(token_id="x", limit_per_minute=None, now=1000.0) is True
    assert await limiter.consume(token_id="x", limit_per_minute=0, now=1000.0) is True
    client.evalsha.assert_not_awaited()


@pytest.mark.asyncio
async def test_drop_removes_bucket(
    limiter: RedisRateLimiter,
    client: MagicMock,
    simulator: _SlidingWindowSimulator,
) -> None:
    """``drop(token_id)`` issues a Redis ``DELETE`` and clears the window."""
    await limiter.consume(token_id="dropme", limit_per_minute=2, now=1000.0)
    await limiter.consume(token_id="dropme", limit_per_minute=2, now=1000.0)
    assert simulator.windows["rl:dropme"]  # bucket is populated
    await limiter.drop("dropme")
    client.delete.assert_awaited_once_with("rl:dropme")
    assert "rl:dropme" not in simulator.windows


@pytest.mark.asyncio
async def test_script_load_called_lazily_on_first_use(
    limiter: RedisRateLimiter,
    client: MagicMock,
) -> None:
    """The Lua script is loaded once and reused across subsequent calls."""
    await limiter.consume(token_id="lazy", limit_per_minute=5, now=1000.0)
    await limiter.consume(token_id="lazy", limit_per_minute=5, now=1000.0)
    # SCRIPT LOAD fired exactly once across two consume calls -- the
    # cached sha is reused. EVALSHA fired twice.
    assert client.script_load.await_count == 1
    assert client.evalsha.await_count == 2


@pytest.mark.asyncio
async def test_noscript_response_triggers_reload_and_retry(
    client: MagicMock,
    simulator: _SlidingWindowSimulator,
) -> None:
    """A ``NOSCRIPT`` ``ResponseError`` from Redis MUST trigger a reload + retry.

    Models Redis flushing its script cache (``SCRIPT FLUSH``, restart,
    failover). The adapter reloads the script and retries once,
    surfacing the second call's result.
    """
    import redis.asyncio as redis_async

    call_count = {"n": 0}

    async def _evalsha_first_noscript_then_ok(sha, numkeys, key, now, limit):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise redis_async.ResponseError("NOSCRIPT")
        return simulator.evalsha(sha, numkeys, key, now, limit)

    client.evalsha = AsyncMock(side_effect=_evalsha_first_noscript_then_ok)
    # Two SCRIPT LOAD calls: one on the cold path, one after the retry.
    client.script_load = AsyncMock(side_effect=["SHA1", "SHA2"])

    limiter = RedisRateLimiter(client)
    allowed = await limiter.consume(token_id="retry", limit_per_minute=5, now=1000.0)
    assert allowed is True
    assert client.script_load.await_count == 2
    assert client.evalsha.await_count == 2
