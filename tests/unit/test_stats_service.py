# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the corpus-inventory snapshot service."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from flycanon.core.services.stats import StatsService
from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow


@pytest.fixture
def stats_service(repositories):
    return StatsService(
        sources=repositories["source"],
        chunks=repositories["chunk"],
        knowledge=repositories["knowledge"],
        candidates=repositories["candidate"],
        jobs=repositories["ingest_job"],
        costs=repositories["cost"],
    )


_SCOPE: dict[str, str] = {"tenant_id": "default", "workspace_id": "default"}


async def _seed_corpus(repositories) -> None:
    """Deposit a tiny but representative corpus across every counter."""

    src1 = SourceRow(
        id="src-1",
        kind="pdf",
        status="ingested",
        content_sha256="aaaa",
        content_bytes=1024,
        n_chunks=2,
        metadata_json={},
        **_SCOPE,
    )
    src2 = SourceRow(
        id="src-2",
        kind="docx",
        status="failed",
        content_sha256="bbbb",
        content_bytes=512,
        n_chunks=0,
        metadata_json={},
        error_code="parse_failed",
        **_SCOPE,
    )
    async with repositories["source"]._session_factory() as session:
        session.add_all([src1, src2])
        await session.commit()

    item = KnowledgeItemRow(
        id="ki-1",
        status="canonical",
        current_version=1,
        title="Policy 1",
        domain="HR",
        jurisdiction="GLOBAL",
        tags_json=[],
        metadata_json={},
        **_SCOPE,
    )
    async with repositories["knowledge"]._session_factory() as session:
        session.add(item)
        await session.commit()

    version = KnowledgeVersionRow(
        id="kv-1",
        knowledge_item_id="ki-1",
        version=1,
        status="canonical",
        title="Policy 1 v1",
        summary=None,
        body="body",
        domain="HR",
        jurisdiction="GLOBAL",
        tags_json=[],
        metadata_json={},
        **_SCOPE,
    )
    async with repositories["knowledge"]._session_factory() as session:
        session.add(version)
        await session.commit()

    cand = CandidateRow(
        id="cand-1",
        status="pending",
        source_id="src-1",
        title="Proposed policy",
        body="...",
        domain="HR",
        jurisdiction="GLOBAL",
        tags_json=[],
        citations_json=[],
        metadata_json={},
        **_SCOPE,
    )
    async with repositories["candidate"]._session_factory() as session:
        session.add(cand)
        await session.commit()

    chunk_embedded = KnowledgeChunkRow(
        id="chunk-1",
        source_id="src-1",
        index_in_source=0,
        total_chunks=2,
        content="hello",
        char_start=0,
        char_end=5,
        embedding_model="dummy",
        embedding=[0.1, 0.2],
        metadata_json={},
        **_SCOPE,
    )
    # NOTE: ``embedding=None`` is explicitly omitted (not set as a
    # kwarg) because the JSON column treats ``None`` as a JSON ``null``
    # literal rather than SQL ``NULL`` on SQLite. Leaving the attribute
    # unset preserves the column-level SQL NULL we need for the
    # embedded-vs-pending split.
    chunk_pending = KnowledgeChunkRow(
        id="chunk-2",
        source_id="src-1",
        index_in_source=1,
        total_chunks=2,
        content="world",
        char_start=6,
        char_end=11,
        metadata_json={},
        **_SCOPE,
    )
    async with repositories["chunk"]._session_factory() as session:
        session.add_all([chunk_embedded, chunk_pending])
        await session.commit()

    job_ok = IngestJobRow(
        id="job-1",
        status="succeeded",
        source_id="src-1",
        attempts=1,
        metadata_json={},
        **_SCOPE,
    )
    job_failed = IngestJobRow(
        id="job-2",
        status="failed",
        attempts=3,
        metadata_json={},
        **_SCOPE,
    )
    async with repositories["ingest_job"]._session_factory() as session:
        session.add_all([job_ok, job_failed])
        await session.commit()

    cost_recent = CostEventRow(
        agent_name="flycanon-answerer",
        model="anthropic:claude",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        cost_usd=Decimal("0.250"),
        occurred_at=datetime.now(UTC),
        **_SCOPE,
    )
    async with repositories["cost"]._session_factory() as session:
        session.add(cost_recent)
        await session.commit()


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_empty_corpus(self, stats_service):
        snap = await stats_service.snapshot()
        assert snap["sources"]["total"] == 0
        assert snap["sources"]["total_bytes"] == 0
        assert snap["knowledge_items"]["total"] == 0
        assert snap["knowledge_versions"] == 0
        assert snap["candidates"]["total"] == 0
        assert snap["chunks"]["total"] == 0
        assert snap["chunks"]["embedded_pct"] == 0.0
        assert snap["ingest_jobs"]["total"] == 0
        assert snap["cost"]["total_events"] == 0

    @pytest.mark.asyncio
    async def test_seeded_corpus_counts(self, stats_service, repositories):
        await _seed_corpus(repositories)
        snap = await stats_service.snapshot()
        assert snap["sources"]["total"] == 2
        assert snap["sources"]["by_kind"] == {"pdf": 1, "docx": 1}
        assert snap["sources"]["by_status"] == {"ingested": 1, "failed": 1}
        assert snap["sources"]["total_bytes"] == 1536
        assert snap["knowledge_items"]["total"] == 1
        assert snap["knowledge_items"]["by_domain"] == {"HR": 1}
        assert snap["knowledge_versions"] == 1
        assert snap["candidates"]["total"] == 1
        assert snap["candidates"]["by_status"] == {"pending": 1}
        assert snap["chunks"]["total"] == 2
        assert snap["chunks"]["embedded"] == 1
        assert snap["chunks"]["embedded_pct"] == 50.0
        assert snap["ingest_jobs"]["total"] == 2
        assert snap["ingest_jobs"]["by_status"] == {"succeeded": 1, "failed": 1}
        assert snap["ingest_jobs"]["avg_attempts"] == 2.0
        assert snap["cost"]["total_events"] == 1
        assert snap["cost"]["cost_usd_24h"] == "0.250000"
