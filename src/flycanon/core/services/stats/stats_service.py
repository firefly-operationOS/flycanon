# Copyright 2026 Firefly Software Solutions Inc
"""``StatsService`` -- single-shot corpus inventory snapshot.

The service backs ``GET /api/v1/stats``: one query per category,
returned in one envelope so dashboards can render a "what's in the
canon right now?" panel without N round-trips.

Aligned with flycanon's scope -- knowledge artefacts (sources,
items, versions, candidates, chunks), the ingest queue, and the
LLM cost stream. No counts that belong in other services (auth /
billing-by-tenant / cluster health) live here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pyfly.container import service
from sqlalchemy import func, select

from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.cost_repository import CostRepository
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


@service
class StatsService:
    """Aggregates corpus + queue + cost counts in one snapshot.

    Each repository exposes its own session factory; here we reach
    through to each one so the inventory call is *one* HTTP request
    no matter how many tables we have to query.
    """

    def __init__(
        self,
        sources: SourceRepository,
        chunks: ChunkRepository,
        knowledge: KnowledgeRepository,
        candidates: CandidateRepository,
        jobs: IngestJobRepository,
        costs: CostRepository,
    ) -> None:
        self._sources = sources
        self._chunks = chunks
        self._knowledge = knowledge
        self._candidates = candidates
        self._jobs = jobs
        self._costs = costs

    async def snapshot(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        sources = await self._source_stats()
        items = await self._knowledge_item_stats()
        versions = await self._version_count()
        candidates = await self._candidate_stats()
        chunks = await self._chunk_stats()
        jobs = await self._job_stats()
        costs = await self._cost_stats(now)
        return {
            "generated_at": now.isoformat(),
            "sources": sources,
            "knowledge_items": items,
            "knowledge_versions": versions,
            "candidates": candidates,
            "chunks": chunks,
            "ingest_jobs": jobs,
            "cost": costs,
        }

    # ------------------------------------------------------------------
    # Sources -- counts by kind + status, total ingested bytes
    # ------------------------------------------------------------------

    async def _source_stats(self) -> dict[str, Any]:
        async with self._sources._session_factory() as session:  # type: ignore[attr-defined]
            total_q = await session.execute(select(func.count()).select_from(SourceRow))
            by_kind = (
                await session.execute(
                    select(SourceRow.kind, func.count(SourceRow.id))
                    .group_by(SourceRow.kind)
                    .order_by(SourceRow.kind)
                )
            ).all()
            by_status = (
                await session.execute(
                    select(SourceRow.status, func.count(SourceRow.id))
                    .group_by(SourceRow.status)
                    .order_by(SourceRow.status)
                )
            ).all()
            bytes_total = (
                await session.execute(
                    select(func.coalesce(func.sum(SourceRow.content_bytes), 0))
                )
            ).scalar_one()
        return {
            "total": int(total_q.scalar_one() or 0),
            "by_kind": {str(k): int(v) for k, v in by_kind},
            "by_status": {str(k): int(v) for k, v in by_status},
            "total_bytes": int(bytes_total or 0),
        }

    # ------------------------------------------------------------------
    # Knowledge items -- counts by status + by domain
    # ------------------------------------------------------------------

    async def _knowledge_item_stats(self) -> dict[str, Any]:
        async with self._knowledge._session_factory() as session:  # type: ignore[attr-defined]
            total = (
                await session.execute(select(func.count()).select_from(KnowledgeItemRow))
            ).scalar_one()
            by_status = (
                await session.execute(
                    select(KnowledgeItemRow.status, func.count(KnowledgeItemRow.id))
                    .group_by(KnowledgeItemRow.status)
                    .order_by(KnowledgeItemRow.status)
                )
            ).all()
            by_domain = (
                await session.execute(
                    select(KnowledgeItemRow.domain, func.count(KnowledgeItemRow.id))
                    .group_by(KnowledgeItemRow.domain)
                    .order_by(KnowledgeItemRow.domain)
                )
            ).all()
        return {
            "total": int(total or 0),
            "by_status": {str(k): int(v) for k, v in by_status},
            "by_domain": {str(k): int(v) for k, v in by_domain},
        }

    async def _version_count(self) -> int:
        async with self._knowledge._session_factory() as session:  # type: ignore[attr-defined]
            row = await session.execute(
                select(func.count()).select_from(KnowledgeVersionRow)
            )
            return int(row.scalar_one() or 0)

    # ------------------------------------------------------------------
    # Candidates -- counts by status
    # ------------------------------------------------------------------

    async def _candidate_stats(self) -> dict[str, Any]:
        async with self._candidates._session_factory() as session:  # type: ignore[attr-defined]
            total = (
                await session.execute(select(func.count()).select_from(CandidateRow))
            ).scalar_one()
            by_status = (
                await session.execute(
                    select(CandidateRow.status, func.count(CandidateRow.id))
                    .group_by(CandidateRow.status)
                    .order_by(CandidateRow.status)
                )
            ).all()
        return {
            "total": int(total or 0),
            "by_status": {str(k): int(v) for k, v in by_status},
        }

    # ------------------------------------------------------------------
    # Chunks -- total + embedded coverage
    # ------------------------------------------------------------------

    async def _chunk_stats(self) -> dict[str, Any]:
        async with self._chunks._session_factory() as session:  # type: ignore[attr-defined]
            total = (
                await session.execute(select(func.count()).select_from(KnowledgeChunkRow))
            ).scalar_one()
            embedded = (
                await session.execute(
                    select(func.count())
                    .select_from(KnowledgeChunkRow)
                    .where(KnowledgeChunkRow.embedding.isnot(None))
                )
            ).scalar_one()
        embedded_pct = (
            round((int(embedded or 0) / int(total)) * 100.0, 1) if total else 0.0
        )
        return {
            "total": int(total or 0),
            "embedded": int(embedded or 0),
            "embedded_pct": embedded_pct,
        }

    # ------------------------------------------------------------------
    # Ingest queue -- counts by status, attempts distribution
    # ------------------------------------------------------------------

    async def _job_stats(self) -> dict[str, Any]:
        async with self._jobs._session_factory() as session:  # type: ignore[attr-defined]
            total = (
                await session.execute(select(func.count()).select_from(IngestJobRow))
            ).scalar_one()
            by_status = (
                await session.execute(
                    select(IngestJobRow.status, func.count(IngestJobRow.id))
                    .group_by(IngestJobRow.status)
                    .order_by(IngestJobRow.status)
                )
            ).all()
            avg_attempts = (
                await session.execute(
                    select(func.coalesce(func.avg(IngestJobRow.attempts), 0.0))
                )
            ).scalar_one()
        return {
            "total": int(total or 0),
            "by_status": {str(k): int(v) for k, v in by_status},
            "avg_attempts": float(avg_attempts or 0.0),
        }

    # ------------------------------------------------------------------
    # Cost stream -- recent totals for the panel
    # ------------------------------------------------------------------

    async def _cost_stats(self, now: datetime) -> dict[str, Any]:
        async with self._costs._session_factory() as session:  # type: ignore[attr-defined]
            total_events = (
                await session.execute(select(func.count()).select_from(CostEventRow))
            ).scalar_one()
            since_24h = now - timedelta(hours=24)
            cost_24h = (
                await session.execute(
                    select(func.coalesce(func.sum(CostEventRow.cost_usd), 0)).where(
                        CostEventRow.occurred_at >= since_24h
                    )
                )
            ).scalar_one()
            since_30d = now - timedelta(days=30)
            cost_30d = (
                await session.execute(
                    select(func.coalesce(func.sum(CostEventRow.cost_usd), 0)).where(
                        CostEventRow.occurred_at >= since_30d
                    )
                )
            ).scalar_one()
        return {
            "total_events": int(total_events or 0),
            "cost_usd_24h": str(cost_24h),
            "cost_usd_30d": str(cost_30d),
        }
