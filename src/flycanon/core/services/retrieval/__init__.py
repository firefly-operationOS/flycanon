# Copyright 2026 Firefly Software Solutions Inc
"""Hybrid retrieval -- BM25 (FTS5) + dense vectors fused via RRF.

The two-store design separates the system-of-record (Postgres
``canon_chunks``) from the retrieval index (the agentic-managed
SQLite corpus + sqlite-vec store):

* Postgres owns provenance, lifecycle metadata, and the full chunk
  content -- this is what audit / replay reads.
* The corpus owns the FTS5 BM25 projection and the dense-vector
  store. Both keep ``chunk_id`` aligned with Postgres so RRF fuses
  ranks correctly.

The :class:`IndexService` writes to both halves on ingest. The
:class:`RetrievalService` reads through the agentic
:class:`HybridRetriever`, hydrates hits with Postgres rows, and
returns :class:`Hit` DTOs ready for the controller.
"""

from __future__ import annotations

from flycanon.core.services.retrieval.corpus_factory import (
    CorpusContext,
    build_corpus_context,
)
from flycanon.core.services.retrieval.index_service import IndexService
from flycanon.core.services.retrieval.retrieval_service import RetrievalService

__all__ = [
    "CorpusContext",
    "IndexService",
    "RetrievalService",
    "build_corpus_context",
]
