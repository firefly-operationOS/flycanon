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

"""Coverage for the ``IngestWorker`` concurrency primitives.

We don't bring up a real EDA bus -- a stub publisher captures the
subscribe-time handler and lets the test invoke it like the real
bus would. This lets us assert on:

* the bounded semaphore (``worker_max_concurrency``);
* the per-handler timeout (``worker_handler_timeout_s``);
* the graceful-shutdown drain (``worker_shutdown_grace_s``);
* the exception-swallowing guard (a single handler failure must
  not propagate up to the bus subscription).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from flycanon.core.services.workers.ingest_worker import IngestWorker

_WORKER_LOGGER = "flycanon.core.services.workers.ingest_worker"


class _CaptureHandler(logging.Handler):
    """Records emitted messages so tests can assert on background-task logs."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def worker_logs():
    """Capture the worker logger directly.

    The dispatcher emits its timeout / failure warnings from a module-level
    logger created at import time. The integration suite runs alembic
    migrations, and ``fileConfig`` historically disabled pre-existing loggers
    (``Logger.disabled = True``), muting later warnings regardless of handlers
    or ``caplog``. Attaching our own handler AND clearing ``disabled`` makes
    the assertion reliable in any suite ordering.
    """
    handler = _CaptureHandler()
    logger = logging.getLogger(_WORKER_LOGGER)
    prev_level, prev_propagate, prev_disabled = logger.level, logger.propagate, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    logger.disabled = False
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.propagate = prev_propagate
        logger.disabled = prev_disabled


class _StubPublisher:
    """Captures the subscribed dispatchers + records start/stop."""

    def __init__(self):
        self.dispatchers: dict[str, list] = {}
        self.started = False
        self.stopped = False

    def subscribe(self, pattern, handler):
        self.dispatchers.setdefault(pattern, []).append(handler)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _settings(**overrides):
    base = dict(
        ingest_topic="flycanon.ingest",
        knowledge_topic="flycanon.knowledge",
        audit_topic="flycanon.audit",
        audit_event="AuditEventRecorded",
        eda_adapter="postgres",
        worker_max_concurrency=2,
        worker_handler_timeout_s=0.5,
        worker_shutdown_grace_s=0.5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_worker(settings):
    return IngestWorker(
        ingestion=MagicMock(),
        repository=MagicMock(),
        event_publisher=_StubPublisher(),
        settings=settings,
    )


def _envelope(event_type="SourceIngested", **payload):
    return SimpleNamespace(event_type=event_type, payload=payload)


class TestSubscription:
    @pytest.mark.asyncio
    async def test_subscribes_to_all_four_event_families(self):
        worker = _make_worker(_settings())

        async def runner():
            asyncio.get_event_loop().call_soon(worker.stop)
            await worker.run_forever()

        await runner()
        pub = worker._publisher  # type: ignore[attr-defined]
        assert "Source*" in pub.dispatchers
        assert "Knowledge*" in pub.dispatchers
        assert "Candidate*" in pub.dispatchers
        assert "AuditEventRecorded" in pub.dispatchers
        assert pub.started and pub.stopped


class TestConcurrencyCap:
    @pytest.mark.asyncio
    async def test_inflight_never_exceeds_max_concurrency(self, monkeypatch):
        settings = _settings(worker_max_concurrency=2, worker_handler_timeout_s=5.0)
        worker = _make_worker(settings)
        seen_peak = 0
        currently_running = 0
        gate = asyncio.Event()

        async def slow_handler(envelope):
            nonlocal currently_running, seen_peak
            currently_running += 1
            seen_peak = max(seen_peak, currently_running)
            try:
                await gate.wait()
            finally:
                currently_running -= 1

        # Monkey-patch the source handler to our slow one.
        monkeypatch.setattr(worker, "_on_source_event", slow_handler)
        dispatcher = worker._make_dispatcher(slow_handler, "source")

        # Fire five events; only two should be inflight at once.
        await asyncio.gather(*(dispatcher(_envelope()) for _ in range(5)))
        # Yield so the scheduled tasks pick up the semaphore.
        await asyncio.sleep(0.05)
        assert currently_running == 2
        gate.set()
        await asyncio.sleep(0.2)
        # All five eventually completed; peak never exceeded 2.
        assert seen_peak == 2


class TestHandlerTimeout:
    @pytest.mark.asyncio
    async def test_hung_handler_is_cancelled_after_timeout(self, worker_logs):
        settings = _settings(worker_handler_timeout_s=0.1)
        worker = _make_worker(settings)

        async def hung_handler(envelope):
            await asyncio.sleep(5.0)

        # Drive the guarded handler inline (the real dispatcher fires it as a
        # fire-and-forget task; awaiting it here makes the timeout deterministic
        # and independent of background-task scheduling). ``wait_for`` cancels
        # the hung handler at the timeout and the outcome is logged.
        await worker._guarded_handler(hung_handler, _envelope(), "source")
        assert any("timed out" in m for m in worker_logs.messages)


class TestExceptionSwallowed:
    @pytest.mark.asyncio
    async def test_handler_failure_does_not_propagate_to_dispatcher(self, worker_logs):
        worker = _make_worker(_settings())

        async def boom(envelope):
            raise RuntimeError("boom")

        # The guard catches the handler failure and logs it instead of
        # re-raising; awaiting it MUST NOT raise -- otherwise the dispatcher
        # that wraps it would propagate the error and the bus subscription
        # would lose this handler on the next delivery.
        await worker._guarded_handler(boom, _envelope(), "source")
        assert any("worker handler failed" in m for m in worker_logs.messages)


class TestShutdownDrain:
    @pytest.mark.asyncio
    async def test_run_forever_waits_for_inflight_before_stopping_bus(self):
        settings = _settings(worker_handler_timeout_s=5.0, worker_shutdown_grace_s=1.0)
        worker = _make_worker(settings)
        finished = asyncio.Event()

        async def slow_ok(envelope):
            await asyncio.sleep(0.2)
            finished.set()

        dispatcher = worker._make_dispatcher(slow_ok, "source")

        async def driver():
            # Fire one slow handler, then stop -- shutdown must wait
            # for the inflight handler to finish before tearing the
            # bus down.
            await dispatcher(_envelope())
            await asyncio.sleep(0.01)
            worker.stop()

        # Run the worker concurrently with the driver.
        await asyncio.gather(driver(), worker.run_forever())
        assert finished.is_set()
        assert worker._publisher.stopped  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_run_forever_cancels_inflight_after_grace_expires(self):
        settings = _settings(worker_handler_timeout_s=10.0, worker_shutdown_grace_s=0.1)
        worker = _make_worker(settings)

        async def never_returns(envelope):
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                raise

        dispatcher = worker._make_dispatcher(never_returns, "source")

        async def driver():
            await dispatcher(_envelope())
            await asyncio.sleep(0.01)
            worker.stop()

        await asyncio.gather(driver(), worker.run_forever())
        # The publisher's stop() still ran -- the worker didn't hang
        # on the inflight handler.
        assert worker._publisher.stopped  # type: ignore[attr-defined]
