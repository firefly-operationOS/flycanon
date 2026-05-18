# Copyright 2026 Firefly Software Solutions Inc
"""EDA workers.

The :class:`IngestWorker` subscribes to the lifecycle topics so an
operator running the worker container sees the same activity stream
the API emits. Today the worker is observation-only -- the canonical
ingestion path is sync from the controller -- but the lifecycle hook
exists so deferred / reprocessing logic can land later without
moving infrastructure.
"""

from __future__ import annotations

from flycanon.core.services.workers.ingest_worker import IngestWorker

__all__ = ["IngestWorker"]
