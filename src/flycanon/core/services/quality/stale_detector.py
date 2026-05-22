# Copyright 2026 Firefly Software Solutions Inc
"""Stale knowledge detection.

Canonical knowledge ages. A SOP written in 2024 referencing a
deprecated API gradually becomes wrong but nothing in the system
notices today. The :class:`StaleDetector` computes a per-item
**staleness score** by:

1. Re-embedding the current knowledge_version body.
2. Cosine-similaring it against the embeddings of the N most
   recent sources whose domain matches.
3. Returning ``1 - max(similarity)`` -- HIGH score = drift, the
   live canon and the fresh sources disagree.

Scoring is lazy. The first call computes + caches the score on
``KnowledgeItemRow.metadata_json.staleness``; the second call
within ``cache_ttl_s`` returns the cached value. A future
follow-up wires a scheduled refresh.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

from pyfly.container import service

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings import EmbeddingService
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 6 * 3600


@service
class StaleDetector:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        embeddings: EmbeddingService,
        settings: CanonSettings,
    ) -> None:
        self._knowledge = knowledge_repository
        self._sources = source_repository
        self._chunks = chunk_repository
        self._embeddings = embeddings
        self._settings = settings

    async def score(
        self,
        item: KnowledgeItemRow,
        *,
        max_recent_sources: int = 20,
    ) -> dict:
        """Compute (or refresh) the staleness score for ``item``.

        Returns a dict the caller stores on ``metadata_json.staleness``:

            {
              "score": 0.42,           # 0 = fresh, 1 = max drift
              "max_similarity": 0.58,  # best cosine vs recent sources
              "sample_size": 12,       # sources compared against
              "computed_at": "2026-05-18T22:11:32+00:00"
            }

        When no recent same-domain sources exist, returns a stub
        with ``sample_size=0`` so the caller can render "no signal
        yet" rather than a misleading score.
        """
        cached = (item.metadata_json or {}).get("staleness")
        if cached and _is_fresh(cached):
            return cached

        version = await self._knowledge.get_version(
            item.id,
            item.current_version,
            tenant_id=item.tenant_id,
            workspace_id=item.workspace_id,
        )
        if version is None or not version.body:
            return _empty_score()

        # Embed the current version's body. We deliberately use the
        # raw body (not its chunks) because the staleness score is
        # an item-level signal, not a chunk-level one.
        try:
            target_vec = await self._embeddings.embed_one(version.body[:8000])
        except Exception as exc:  # noqa: BLE001
            logger.warning("stale: failed to embed version body: %s", exc)
            return _empty_score()

        # Pull the most recently ingested sources matching the
        # item's domain. We compare against per-chunk embeddings
        # (the source's first chunk is a reasonable proxy for the
        # source-level vector).
        recent_sources, _total = await self._sources.list_sources(
            statuses=["ingested"],
            limit=max_recent_sources,
        )
        if not recent_sources:
            return _empty_score()

        # Filter by domain when the source carries a matching hint.
        scoped = [s for s in recent_sources if (s.metadata_json or {}).get("domain") in (None, item.domain)]
        if not scoped:
            return _empty_score()

        max_sim = 0.0
        sample_size = 0
        for source in scoped[:max_recent_sources]:
            chunks = await self._chunks.list_for_source(source.id)
            if not chunks:
                continue
            # First chunk is a reasonable source-level proxy.
            embedding = getattr(chunks[0], "embedding", None)
            if embedding is None:
                continue
            sim = _cosine(target_vec, list(embedding))
            max_sim = max(max_sim, sim)
            sample_size += 1
            if sample_size >= max_recent_sources:
                break

        if sample_size == 0:
            return _empty_score()

        score = max(0.0, 1.0 - max_sim)
        computed = {
            "score": round(float(score), 4),
            "max_similarity": round(float(max_sim), 4),
            "sample_size": sample_size,
            "computed_at": datetime.now(UTC).isoformat(),
        }
        # Cache on the item row so the next call is a no-op.
        metadata = dict(item.metadata_json or {})
        metadata["staleness"] = computed
        item.metadata_json = metadata
        await self._knowledge.upsert_item(item)
        return computed


def _is_fresh(cached: dict) -> bool:
    """Return True when a cached score is still inside the TTL."""
    raw = cached.get("computed_at")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return datetime.now(UTC) - when < timedelta(seconds=_CACHE_TTL_S)


def _empty_score() -> dict:
    return {
        "score": None,
        "max_similarity": None,
        "sample_size": 0,
        "computed_at": datetime.now(UTC).isoformat(),
    }


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
