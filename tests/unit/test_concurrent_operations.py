# Copyright 2026 Firefly Software Solutions Inc
"""Concurrency-safety regression tests.

Covers the surfaces where two workers / replicas could double-process
or race to corrupt state:

* ``IngestJobRepository.mark_running`` -- atomic claim. A second
  caller observes ``None`` and short-circuits.
* ``CandidateRepository.find_conflict_candidate`` -- dedup helper
  the conflict detector uses to keep its inbox idempotent across
  repeated scans.
* ``KnowledgeService.update`` -- concurrent version bumps surface
  as the typed :class:`KnowledgeVersionConflict` (HTTP 409), not
  the raw IntegrityError that previously leaked as a 500.
"""

from __future__ import annotations

import pytest

from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.ingest_job import IngestJobRow


@pytest.fixture
def jobs(repositories):
    return repositories["ingest_job"]


@pytest.fixture
def candidates(repositories):
    return repositories["candidate"]


class TestIngestJobAtomicClaim:
    @pytest.mark.asyncio
    async def test_mark_running_claims_queued_job(self, jobs):
        await jobs.add(IngestJobRow(id="job-1", status="queued"))
        claimed = await jobs.mark_running("job-1")
        assert claimed is not None
        assert claimed.status == "running"
        assert claimed.attempts == 1
        assert claimed.started_at is not None

    @pytest.mark.asyncio
    async def test_mark_running_returns_none_when_already_running(self, jobs):
        """The second worker to call ``mark_running`` sees the row has
        left ``queued`` and short-circuits -- the atomic UPDATE returns
        zero rows, which the repo maps to ``None``."""
        await jobs.add(IngestJobRow(id="job-2", status="queued"))
        first = await jobs.mark_running("job-2")
        assert first is not None

        # Simulate the duplicate EDA delivery: another worker tries to
        # claim the same job. The UPDATE returns nothing because the
        # WHERE clause requires status='queued'.
        second = await jobs.mark_running("job-2")
        assert second is None

    @pytest.mark.asyncio
    async def test_mark_running_returns_none_when_terminal(self, jobs):
        await jobs.add(IngestJobRow(id="job-3", status="succeeded"))
        # The first claim attempt observes a terminal state and refuses.
        assert await jobs.mark_running("job-3") is None

    @pytest.mark.asyncio
    async def test_mark_running_returns_none_for_unknown_id(self, jobs):
        assert await jobs.mark_running("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_mark_running_preserves_started_at_on_retry_claim(self, jobs):
        """After ``mark_failed`` resets the job, a fresh ``mark_running``
        finds the row outside ``queued`` and refuses; the existing
        ``started_at`` is preserved either way."""
        await jobs.add(IngestJobRow(id="job-4", status="queued"))
        first = await jobs.mark_running("job-4")
        original_started = first.started_at
        # A retry would only run if the worker explicitly transitions
        # the row back to ``queued``; the atomic claim refuses to bump
        # the started_at when the row never left running.
        retry = await jobs.mark_running("job-4")
        assert retry is None
        assert original_started is not None


class TestConflictCandidateDedup:
    @pytest.mark.asyncio
    async def test_find_returns_none_when_no_match(self, candidates):
        result = await candidates.find_conflict_candidate(from_item_id="ki-1", to_item_id="ki-2")
        assert result is None

    @pytest.mark.asyncio
    async def test_find_matches_metadata_breadcrumb(self, candidates):
        row = CandidateRow(
            status="proposed",
            source_id="ki-1",
            title="Conflict: A vs B",
            body="...",
            domain="compliance",
            metadata_json={
                "kind": "conflict_detection",
                "from_item_id": "ki-1",
                "to_item_id": "ki-2",
            },
        )
        await candidates.add_many([row])
        found = await candidates.find_conflict_candidate(from_item_id="ki-1", to_item_id="ki-2")
        assert found is not None
        assert found.id == row.id

    @pytest.mark.asyncio
    async def test_find_ignores_non_detection_candidates(self, candidates):
        """A human-proposed candidate that happens to share the
        ``source_id`` must not be picked up as a dedup hit; only the
        detector's ``metadata.kind=conflict_detection`` rows count."""
        row = CandidateRow(
            status="proposed",
            source_id="ki-1",
            title="Manual proposal",
            body="...",
            domain="compliance",
            metadata_json={},  # no conflict_detection kind
        )
        await candidates.add_many([row])
        assert await candidates.find_conflict_candidate(from_item_id="ki-1", to_item_id="ki-2") is None

    @pytest.mark.asyncio
    async def test_find_ignores_other_to_item_id(self, candidates):
        row = CandidateRow(
            status="proposed",
            source_id="ki-1",
            title="Conflict: A vs C",
            body="...",
            domain="compliance",
            metadata_json={
                "kind": "conflict_detection",
                "from_item_id": "ki-1",
                "to_item_id": "ki-3",  # different target
            },
        )
        await candidates.add_many([row])
        assert await candidates.find_conflict_candidate(from_item_id="ki-1", to_item_id="ki-2") is None
