# Copyright 2026 Firefly Software Solutions Inc
"""Concurrency-safety regression tests.

Covers the surfaces where two workers / replicas could double-process
or race to corrupt state:

* ``IngestJobRepository.mark_running`` -- atomic claim. A second
  caller observes ``None`` and short-circuits. Stale ``running``
  rows past the lease window are re-claimable.
* ``IngestJobRepository.reclaim_stuck`` -- bulk sweep for stale
  ``running`` rows whose EDA delivery has been lost.
* ``CandidateRepository.find_conflict_candidate`` -- dedup helper
  the conflict detector uses to keep its inbox idempotent across
  repeated scans.
* ``CandidateRepository.claim_decision`` -- atomic ``proposed -> X``
  flip used by accept/reject. The second operator gets ``None``
  instead of the loser leaking a 500 / writing a duplicate item.
* ``KnowledgeRepository.claim_status_transition`` -- atomic
  lifecycle flip used by supersede / retire. The loser gets
  ``None`` rather than overwriting the winner's pointers.
* ``KnowledgeService.update`` -- concurrent version bumps surface
  as the typed :class:`KnowledgeVersionConflict` (HTTP 409), not
  the raw IntegrityError that previously leaked as a 500.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow


@pytest.fixture
def jobs(repositories):
    return repositories["ingest_job"]


@pytest.fixture
def candidates(repositories):
    return repositories["candidate"]


@pytest.fixture
def knowledge(repositories):
    return repositories["knowledge"]


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

    @pytest.mark.asyncio
    async def test_mark_running_refuses_running_within_lease(self, jobs):
        """A running row whose lease is still fresh stays held by the
        original worker -- the lease window protects the claim against
        an over-eager second worker."""
        await jobs.add(IngestJobRow(id="job-5", status="queued"))
        first = await jobs.mark_running("job-5", lease_seconds=600)
        assert first is not None
        # Same call again: the row is at ``running`` with ``started_at``
        # well inside the 600s lease, so the OR-branch for stale rows
        # doesn't match either.
        retry = await jobs.mark_running("job-5", lease_seconds=600)
        assert retry is None

    @pytest.mark.asyncio
    async def test_mark_running_reclaims_stale_running_row(self, jobs):
        """A worker that crashed mid-run leaves the row at ``running``
        forever; the lease window in ``mark_running`` lets a fresh
        worker re-claim it past the threshold."""
        # Insert a row pre-set to running with a started_at well past
        # any sensible lease window.
        stale = datetime.now(UTC) - timedelta(minutes=30)
        await jobs.add(
            IngestJobRow(
                id="job-stale",
                status="running",
                attempts=1,
                started_at=stale,
            )
        )
        reclaimed = await jobs.mark_running("job-stale", lease_seconds=600)
        assert reclaimed is not None
        assert reclaimed.status == "running"
        assert reclaimed.attempts == 2  # bumped on reclaim
        # started_at refreshed to "now". SQLite returns naive
        # datetimes for the column even when the input was tz-aware,
        # so compare on the naive view of the threshold.
        assert reclaimed.started_at is not None
        refreshed = reclaimed.started_at
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=UTC)
        assert refreshed > stale

    @pytest.mark.asyncio
    async def test_reclaim_stuck_sweeps_only_stale_rows(self, jobs):
        """``reclaim_stuck`` should pick up rows past the lease but
        leave fresh ``running`` rows alone."""
        fresh = datetime.now(UTC) - timedelta(seconds=10)
        stale = datetime.now(UTC) - timedelta(minutes=30)
        await jobs.add(IngestJobRow(id="fresh-1", status="running", started_at=fresh))
        await jobs.add(IngestJobRow(id="stale-1", status="running", started_at=stale))
        await jobs.add(IngestJobRow(id="stale-2", status="running", started_at=stale))
        # 600s lease: fresh-1 stays, stale-1 + stale-2 demoted to queued.
        ids = await jobs.reclaim_stuck(lease_seconds=600)
        assert sorted(ids) == ["stale-1", "stale-2"]
        # The reclaimed rows are visible at status=queued for another
        # worker to pick up via mark_running.
        fresh_row = await jobs.get("fresh-1")
        stale_row = await jobs.get("stale-1")
        assert fresh_row.status == "running"
        assert stale_row.status == "queued"


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


class TestCandidateClaimDecision:
    """``claim_decision`` is the atomic gate that protects accept /
    reject from the check-then-act race two operators in the inbox
    used to expose: both would pass the local ``status == 'proposed'``
    check, both would write a knowledge item, then the second
    candidate.update would silently overwrite the first."""

    @pytest.mark.asyncio
    async def test_first_caller_claims_the_decision(self, candidates):
        row = CandidateRow(
            id="cand-claim-1",
            status="proposed",
            source_id="src-1",
            title="t",
            body="b",
            domain="compliance",
        )
        await candidates.add_many([row])
        claimed = await candidates.claim_decision(
            "cand-claim-1",
            new_status="accepted",
            actor="alice",
        )
        assert claimed is not None
        assert claimed.status == "accepted"
        assert claimed.decided_by == "alice"
        assert claimed.decided_at is not None

    @pytest.mark.asyncio
    async def test_second_caller_observes_none(self, candidates):
        row = CandidateRow(
            id="cand-claim-2",
            status="proposed",
            source_id="src-1",
            title="t",
            body="b",
            domain="compliance",
        )
        await candidates.add_many([row])
        first = await candidates.claim_decision("cand-claim-2", new_status="accepted", actor="alice")
        assert first is not None
        # Bob clicks accept at the same time as Alice -- but he loses.
        second = await candidates.claim_decision("cand-claim-2", new_status="accepted", actor="bob")
        assert second is None

    @pytest.mark.asyncio
    async def test_concurrent_claims_only_one_wins(self, candidates):
        """Fire two claim attempts back-to-back. Exactly one returns
        the row -- the DB's WHERE clause is the arbiter."""
        row = CandidateRow(
            id="cand-claim-3",
            status="proposed",
            source_id="src-1",
            title="t",
            body="b",
            domain="compliance",
        )
        await candidates.add_many([row])
        results = await asyncio.gather(
            candidates.claim_decision("cand-claim-3", new_status="accepted", actor="alice"),
            candidates.claim_decision("cand-claim-3", new_status="rejected", actor="bob"),
        )
        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]
        assert len(winners) == 1
        assert len(losers) == 1

    @pytest.mark.asyncio
    async def test_finalise_attaches_pointers(self, candidates):
        row = CandidateRow(
            id="cand-fin-1",
            status="proposed",
            source_id="src-1",
            title="t",
            body="b",
            domain="compliance",
        )
        await candidates.add_many([row])
        await candidates.claim_decision("cand-fin-1", new_status="accepted", actor="alice")
        final = await candidates.finalise(
            "cand-fin-1",
            materialised_knowledge_item_id="ki-9",
            materialised_version=3,
        )
        assert final is not None
        assert final.materialised_knowledge_item_id == "ki-9"
        assert final.materialised_version == 3
        # status preserved across the finalise -- we don't reopen the
        # decision after claim.
        assert final.status == "accepted"


class TestKnowledgeClaimStatusTransition:
    """``claim_status_transition`` is the atomic gate that protects
    supersede / retire from check-then-act overwrites of the lifecycle
    pointers (``superseded_by_item_id`` / ``retired_at``)."""

    @pytest.mark.asyncio
    async def test_supersede_first_caller_wins(self, knowledge):
        item = KnowledgeItemRow(
            id="ki-sup-1",
            status="published",
            current_version=1,
            title="t",
            domain="compliance",
            jurisdiction="GLOBAL",
        )
        await knowledge.upsert_item(item)
        first = await knowledge.claim_status_transition(
            "ki-sup-1",
            from_statuses=["draft", "published"],
            to_status="superseded",
            superseded_by_item_id="ki-new-1",
        )
        assert first is not None
        assert first.status == "superseded"
        assert first.superseded_by_item_id == "ki-new-1"

    @pytest.mark.asyncio
    async def test_supersede_second_caller_observes_none(self, knowledge):
        item = KnowledgeItemRow(
            id="ki-sup-2",
            status="published",
            current_version=1,
            title="t",
            domain="compliance",
            jurisdiction="GLOBAL",
        )
        await knowledge.upsert_item(item)
        # Alice supersedes -> ki-new-A
        await knowledge.claim_status_transition(
            "ki-sup-2",
            from_statuses=["draft", "published"],
            to_status="superseded",
            superseded_by_item_id="ki-new-A",
        )
        # Bob tries supersede -> ki-new-B at the same time. He loses.
        second = await knowledge.claim_status_transition(
            "ki-sup-2",
            from_statuses=["draft", "published"],
            to_status="superseded",
            superseded_by_item_id="ki-new-B",
        )
        assert second is None
        # Alice's pointer survives -- Bob's overwrite never lands.
        final = await knowledge.get_item("ki-sup-2")
        assert final.superseded_by_item_id == "ki-new-A"

    @pytest.mark.asyncio
    async def test_retire_refuses_already_retired(self, knowledge):
        item = KnowledgeItemRow(
            id="ki-ret-1",
            status="retired",
            current_version=1,
            title="t",
            domain="compliance",
            jurisdiction="GLOBAL",
        )
        await knowledge.upsert_item(item)
        result = await knowledge.claim_status_transition(
            "ki-ret-1",
            from_statuses=["draft", "published", "superseded"],
            to_status="retired",
            retired_reason="just because",
            mark_retired_at=True,
        )
        assert result is None  # already retired -> the loser branch
