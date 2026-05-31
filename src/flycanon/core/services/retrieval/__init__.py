# Copyright 2026 Firefly Software Solutions Inc
"""Hybrid retrieval -- Postgres-native BM25 + dense vectors fused via RRF.

Retrieval is Postgres-native end to end:

* The BM25 projection rides on a GENERATED ``tsv`` column (with a GIN
  index) on ``canon_chunks`` -- the same table that owns provenance,
  lifecycle metadata, and the full chunk content read by audit /
  replay.
* Dense vectors live in pgvector on the canonical Postgres. Both keep
  ``chunk_id`` aligned so RRF fuses ranks correctly.

The :class:`IndexService` writes the dense projection on ingest. The
:class:`RetrievalService` reads through :class:`HybridRetriever`,
hydrates hits with Postgres rows, and returns :class:`Hit` DTOs ready
for the controller.
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
