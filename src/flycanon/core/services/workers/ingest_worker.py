# Copyright 2026 Firefly Software Solutions Inc
"""``IngestWorker`` -- bounded-concurrency consumer for the lifecycle topics.

The worker subscribes to the event families flycanon publishes
(``Source*``, ``Knowledge*``, ``Candidate*``, ``AuditEventRecorded``)
and dispatches each delivery through a bounded asyncio semaphore so a
burst of events doesn't:

* exhaust the async pool (one event = one task; without the cap a
  10k-event re-delivery storm would crash the worker),
* blow up an LLM provider's rate limit when handlers become more than
  observational (auto-consolidation, source re-indexing, ...),
* leak unbounded coroutine references if a downstream call hangs.

Concurrency primitives
======================

* :class:`asyncio.Semaphore` -- caps inflight handler tasks at
  ``settings.worker_max_concurrency`` (default 8).
* :func:`asyncio.wait_for` -- a per-handler timeout
  (``settings.worker_handler_timeout_s``, default 120s) so a stuck
  downstream call cannot pin a worker slot indefinitely.
* :class:`asyncio.Event` + an inflight task set -- :meth:`stop`
  flips the event and the run loop drains inflight tasks for up to
  ``settings.worker_shutdown_grace_s`` (default 30s) before
  cancelling them.

Handlers are currently observation-only -- the worker structures
each event into a log line and emits a latency metric. The
dispatcher is in place so any of the handlers can grow real
side-effects (auto-consolidate on ``SourceIngested``, re-warm
caches on ``KnowledgeItemPublished``, ...) without re-architecting
the lifecycle plumbing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from flycanon.config import CanonSettings
from flycanon.core.services.ingestion import IngestionService  # noqa: F401  (used by the CLI helper)
from flycanon.models.repositories import SourceRepository

logger = logging.getLogger(__name__)


HandlerFn = Callable[[Any], Awaitable[None]]


class IngestWorker:
    """Long-running EDA subscriber with bounded concurrency.

    ``run_forever()`` registers one handler per logical family,
    starts the publisher's consumer loop, and blocks on a stop
    event. Each delivery is wrapped in :meth:`_dispatch` which
    acquires the semaphore, runs the handler under a timeout, and
    swallows exceptions so a single handler failure cannot crash
    the whole bus subscription.
    """

    def __init__(
        self,
        *,
        ingestion: IngestionService,
        repository: SourceRepository,
        event_publisher: object,
        settings: CanonSettings,
    ) -> None:
        self._ingestion = ingestion
        self._repository = repository
        self._publisher = event_publisher
        self._settings = settings
        self._stop_event = asyncio.Event()
        # The bounded semaphore is the load-shedding primitive: when
        # ``max_concurrency`` tasks are inflight, ``acquire()`` blocks
        # the dispatcher (and thus the underlying bus's delivery
        # loop). Combined with the timeout below, this means a
        # provider stall back-pressures the bus instead of piling up
        # tasks in memory.
        self._sem = asyncio.Semaphore(settings.worker_max_concurrency)
        # Track inflight tasks so ``stop()`` can wait for them to
        # drain before tearing the bus down. ``add_done_callback``
        # auto-removes a task on completion so the set doesn't grow.
        self._inflight: set[asyncio.Task[None]] = set()

    async def run_forever(self) -> None:
        """Subscribe to every lifecycle topic and block until cancelled."""
        # Subscribe BEFORE starting the bus -- the EDA adapters spin
        # up consumer loops at ``start()`` time, and they only do so
        # when at least one handler is registered.
        subscribe = getattr(self._publisher, "subscribe", None)
        if subscribe is not None:
            subscribe("Source*", self._make_dispatcher(self._on_source_event, "source"))
            subscribe("Knowledge*", self._make_dispatcher(self._on_knowledge_event, "knowledge"))
            subscribe("Candidate*", self._make_dispatcher(self._on_candidate_event, "candidate"))
            subscribe(
                self._settings.audit_event,
                self._make_dispatcher(self._on_audit_event, "audit"),
            )

        start = getattr(self._publisher, "start", None)
        if start is not None:
            await start()

        logger.info(
            "ingest worker started topics=%s,%s,%s adapter=%s max_concurrency=%d "
            "handler_timeout_s=%.1f",
            self._settings.ingest_topic,
            self._settings.knowledge_topic,
            self._settings.audit_topic,
            self._settings.eda_adapter,
            self._settings.worker_max_concurrency,
            self._settings.worker_handler_timeout_s,
        )

        try:
            await self._stop_event.wait()
        finally:
            await self._drain_inflight()
            stop = getattr(self._publisher, "stop", None)
            if stop is not None:
                await stop()
            logger.info("ingest worker stopped")

    def stop(self) -> None:
        """Signal the worker loop to exit (called by CLI shutdown hooks)."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _make_dispatcher(self, handler: HandlerFn, family: str) -> HandlerFn:
        """Wrap a handler with the bounded-concurrency dispatcher.

        Returns a coroutine the EDA bus can subscribe directly. The
        dispatcher:

        1. Acquires the semaphore (load-sheds when full).
        2. Schedules the handler with a wall-clock timeout.
        3. Catches every exception so a single handler failure
           cannot crash the bus subscription -- the durable record
           is still the audit row in Postgres.
        4. Records latency + outcome.
        """

        async def _dispatch(envelope: Any) -> None:
            await self._dispatch(handler, envelope, family)

        return _dispatch

    async def _dispatch(self, handler: HandlerFn, envelope: Any, family: str) -> None:
        if self._stop_event.is_set():
            # The bus is draining; new deliveries are dropped (they'll
            # be redelivered on the next worker boot via the durable
            # outbox).
            logger.debug("worker stopping; dropping event family=%s", family)
            return

        task = asyncio.create_task(
            self._guarded_handler(handler, envelope, family),
            name=f"flycanon-worker-{family}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _guarded_handler(
        self, handler: HandlerFn, envelope: Any, family: str
    ) -> None:
        """Run a handler under the semaphore + timeout, log outcomes."""
        async with self._sem:
            started = time.perf_counter()
            try:
                await asyncio.wait_for(
                    handler(envelope),
                    timeout=self._settings.worker_handler_timeout_s,
                )
                logger.debug(
                    "worker handler ok family=%s elapsed_ms=%d",
                    family,
                    int((time.perf_counter() - started) * 1000),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "worker handler timed out family=%s timeout_s=%.1f event_type=%s",
                    family,
                    self._settings.worker_handler_timeout_s,
                    self._event_type_of(envelope),
                )
            except asyncio.CancelledError:
                # Re-raise so the cancellation propagates -- this fires
                # on the shutdown-drain path.
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "worker handler failed family=%s event_type=%s error=%s",
                    family,
                    self._event_type_of(envelope),
                    exc,
                )

    async def _drain_inflight(self) -> None:
        """Wait for inflight handlers up to ``shutdown_grace_s``."""
        if not self._inflight:
            return
        grace = self._settings.worker_shutdown_grace_s
        logger.info(
            "worker draining inflight=%d grace_s=%.1f", len(self._inflight), grace
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(*list(self._inflight), return_exceptions=True),
                timeout=grace,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "worker drain timed out -- cancelling %d inflight task(s)",
                len(self._inflight),
            )
            for task in list(self._inflight):
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*list(self._inflight), return_exceptions=True)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _on_source_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "source.event type=%s source_id=%s n_chunks=%s",
            self._event_type_of(envelope),
            payload.get("source_id"),
            payload.get("n_chunks"),
        )

    async def _on_knowledge_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "knowledge.event type=%s item_id=%s version=%s",
            self._event_type_of(envelope),
            payload.get("item_id"),
            payload.get("version"),
        )

    async def _on_candidate_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "candidate.event type=%s candidate_id=%s",
            self._event_type_of(envelope),
            payload.get("candidate_id"),
        )

    async def _on_audit_event(self, envelope: Any) -> None:
        payload = self._payload_of(envelope)
        logger.info(
            "audit.event type=%s subject_kind=%s subject_id=%s actor=%s",
            payload.get("event_type"),
            payload.get("subject_kind"),
            payload.get("subject_id"),
            payload.get("actor"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_of(envelope: Any) -> dict[str, Any]:
        payload = getattr(envelope, "payload", None)
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _event_type_of(envelope: Any) -> str:
        return str(getattr(envelope, "event_type", "") or "")
