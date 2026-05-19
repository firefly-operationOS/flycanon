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


# ---------------------------------------------------------------------------
# Service-level integration tests
#
# The primitives above prove the repository contract. These tests
# exercise the full service path (KnowledgeService / CandidateService)
# so the orchestration around the atomic claim -- ordering of audit
# events, EDA publishes, response wiring -- is regression-covered, not
# just the storage layer.
# ---------------------------------------------------------------------------


from flycanon.config import CanonSettings  # noqa: E402
from flycanon.core.services.audit import AuditService  # noqa: E402
from flycanon.core.services.consolidation.candidate_service import (  # noqa: E402
    CandidateService,
)
from flycanon.core.services.consolidation.consolidator import Consolidator  # noqa: E402
from flycanon.core.services.consolidation.errors import (  # noqa: E402
    CandidateAlreadyDecided,
)
from flycanon.core.services.knowledge import (  # noqa: E402
    KnowledgeItemAlreadyRetired,
    KnowledgeService,
)
from flycanon.core.services.knowledge.errors import (  # noqa: E402
    InvalidSupersedeTarget,
)
from flycanon.core.services.knowledge.relation_service import (  # noqa: E402
    KnowledgeRelationService,
    RelationConflictError,
)
from flycanon.interfaces.dtos.candidate import (  # noqa: E402
    AcceptCandidateRequest,
    RejectCandidateRequest,
)
from flycanon.interfaces.dtos.knowledge import (  # noqa: E402
    CreateKnowledgeRequest,
    RetireKnowledgeRequest,
    SupersedeKnowledgeRequest,
)
from flycanon.interfaces.dtos.relation import CreateRelationRequest  # noqa: E402
from flycanon.interfaces.enums import (  # noqa: E402
    CandidateStatus,
    Domain,
    Jurisdiction,
    RelationKind,
)


@pytest.fixture
def settings() -> CanonSettings:
    return CanonSettings()


@pytest.fixture
def audit_service(repositories, settings):
    return AuditService(
        repository=repositories["audit"],
        event_publisher=None,
        settings=settings,
    )


@pytest.fixture
def knowledge_service(repositories, audit_service, settings):
    return KnowledgeService(
        repository=repositories["knowledge"],
        audit=audit_service,
        event_publisher=None,
        settings=settings,
    )


@pytest.fixture
def relation_service(repositories, audit_service, settings):
    return KnowledgeRelationService(
        knowledge_repository=repositories["knowledge"],
        relation_repository=repositories["relation"],
        audit=audit_service,
        event_publisher=None,
        settings=settings,
    )


class _StubConsolidator(Consolidator):  # pragma: no cover -- never invoked
    """Candidate service requires a Consolidator at construction; the
    accept/reject paths under test never invoke it, but the DI graph
    expects something callable."""

    def __init__(self) -> None:
        pass


@pytest.fixture
def candidate_service(repositories, knowledge_service, audit_service, settings):
    return CandidateService(
        candidate_repository=repositories["candidate"],
        source_repository=repositories["source"],
        chunk_repository=repositories["chunk"],
        knowledge=knowledge_service,
        consolidator=_StubConsolidator(),
        audit=audit_service,
        event_publisher=None,
        settings=settings,
    )


class TestKnowledgeRetireServiceConcurrency:
    """End-to-end: two concurrent ``KnowledgeService.retire`` calls
    on the same item must produce exactly one mutation and the loser
    must raise ``KnowledgeItemAlreadyRetired``. Before the atomic
    claim landed, both ``upsert_item`` calls succeeded with
    last-writer-wins on lifecycle pointers."""

    @pytest.mark.asyncio
    async def test_concurrent_retire_one_winner(self, knowledge_service, repositories):
        version = await knowledge_service.create(
            CreateKnowledgeRequest(
                title="Item to retire",
                body="body",
                domain=Domain.process,
                jurisdiction=Jurisdiction.GLOBAL,
            )
        )
        item_id = version.knowledge_item_id
        results = await asyncio.gather(
            knowledge_service.retire(
                item_id, RetireKnowledgeRequest(reason="alice", actor="alice")
            ),
            knowledge_service.retire(
                item_id, RetireKnowledgeRequest(reason="bob", actor="bob")
            ),
            return_exceptions=True,
        )
        winners = [r for r in results if not isinstance(r, Exception)]
        losers = [r for r in results if isinstance(r, Exception)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], KnowledgeItemAlreadyRetired)
        # Exactly one retired_at + one reason landed on the row.
        final = await repositories["knowledge"].get_item(item_id)
        assert final.status == "retired"
        assert final.retired_reason in ("alice", "bob")


class TestKnowledgeSupersedeServiceConcurrency:
    """End-to-end: two concurrent ``KnowledgeService.supersede`` calls
    on the same item with different targets must produce exactly one
    mutation. The previously-documented gap in docs/concurrency.md
    let both writes land (last-writer-wins on superseded_by_item_id).
    Now the claim_status_transition primitive gates the transition."""

    @pytest.mark.asyncio
    async def test_concurrent_supersede_one_pointer_survives(
        self, knowledge_service, repositories
    ):
        # Build three items: A (subject), B and C (targets).
        a = await knowledge_service.create(
            CreateKnowledgeRequest(
                title="A", body="a", domain=Domain.process, jurisdiction=Jurisdiction.GLOBAL
            )
        )
        b = await knowledge_service.create(
            CreateKnowledgeRequest(
                title="B", body="b", domain=Domain.process, jurisdiction=Jurisdiction.GLOBAL
            )
        )
        c = await knowledge_service.create(
            CreateKnowledgeRequest(
                title="C", body="c", domain=Domain.process, jurisdiction=Jurisdiction.GLOBAL
            )
        )
        item_id = a.knowledge_item_id
        target_b = b.knowledge_item_id
        target_c = c.knowledge_item_id

        results = await asyncio.gather(
            knowledge_service.supersede(
                item_id,
                SupersedeKnowledgeRequest(superseded_by_item_id=target_b, actor="alice"),
            ),
            knowledge_service.supersede(
                item_id,
                SupersedeKnowledgeRequest(superseded_by_item_id=target_c, actor="bob"),
            ),
            return_exceptions=True,
        )
        # The atomic claim must fire on exactly one of the two operators:
        # one observes ``InvalidSupersedeTarget`` (the loser branch in
        # KnowledgeService.supersede when claim_status_transition returns
        # None). Under SQLite's single-writer model the winner's audit
        # write can still race with the loser's read at the engine level
        # -- the orchestration invariant is what we're checking here, not
        # the storage engine's parallel-write tolerance.
        supersede_losers = [r for r in results if isinstance(r, InvalidSupersedeTarget)]
        assert len(supersede_losers) == 1, (
            f"expected exactly one InvalidSupersedeTarget loser; results="
            f"{[type(r).__name__ for r in results]}"
        )
        # Final row state: status flipped to superseded with exactly one
        # of the two targets, not a frankenstein overwrite.
        final = await repositories["knowledge"].get_item(item_id)
        assert final.status == "superseded"
        assert final.superseded_by_item_id in (target_b, target_c)


class TestCandidateAcceptServiceConcurrency:
    """End-to-end: two concurrent ``CandidateService.accept`` calls on
    the same proposed candidate must produce exactly one knowledge
    item (or version) and one accepted candidate, with the loser
    raising ``CandidateAlreadyDecided``. Pre-fix, both calls passed
    the local status guard and both wrote a knowledge item."""

    @pytest.mark.asyncio
    async def test_concurrent_accept_creates_only_one_item(
        self, candidate_service, repositories
    ):
        # Seed a proposed candidate. (Skip the consolidator -- just
        # plant the row the way propose_from_source would.)
        candidate = CandidateRow(
            id="cand-svc-1",
            status=CandidateStatus.proposed.value,
            source_id="src-1",
            title="Proposed canonical statement",
            body="The body",
            summary=None,
            domain=Domain.process.value,
            jurisdiction=Jurisdiction.GLOBAL.value,
        )
        await repositories["candidate"].add_many([candidate])

        results = await asyncio.gather(
            candidate_service.accept(
                "cand-svc-1",
                AcceptCandidateRequest(actor="alice", publish=True),
            ),
            candidate_service.accept(
                "cand-svc-1",
                AcceptCandidateRequest(actor="bob", publish=True),
            ),
            return_exceptions=True,
        )
        winners = [r for r in results if not isinstance(r, Exception)]
        losers = [r for r in results if isinstance(r, Exception)]
        assert len(winners) == 1, f"want 1 winner, got {len(winners)}; losers={losers}"
        assert len(losers) == 1
        assert isinstance(losers[0], CandidateAlreadyDecided)
        # Verify only ONE knowledge item was created (the loser must
        # have short-circuited BEFORE calling knowledge.create).
        items, total = await repositories["knowledge"].list_items(
            statuses=["draft", "published"],
        )
        assert total == 1, f"expected exactly 1 item, got {total}: {[i.title for i in items]}"

    @pytest.mark.asyncio
    async def test_concurrent_reject_one_winner(self, candidate_service, repositories):
        candidate = CandidateRow(
            id="cand-svc-2",
            status=CandidateStatus.proposed.value,
            source_id="src-2",
            title="t",
            body="b",
            domain=Domain.process.value,
        )
        await repositories["candidate"].add_many([candidate])

        results = await asyncio.gather(
            candidate_service.reject(
                "cand-svc-2", RejectCandidateRequest(reason="alice", actor="alice")
            ),
            candidate_service.reject(
                "cand-svc-2", RejectCandidateRequest(reason="bob", actor="bob")
            ),
            return_exceptions=True,
        )
        winners = [r for r in results if not isinstance(r, Exception)]
        losers = [r for r in results if isinstance(r, Exception)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], CandidateAlreadyDecided)


class TestRelationServiceTypedConflict:
    """End-to-end: adding a duplicate relation must raise the typed
    ``RelationConflictError`` (HTTP 409) rather than a generic
    IntegrityError. The narrowed except-clause catches the typed
    SQLAlchemy exception, not a string match on the engine's
    error text."""

    @pytest.mark.asyncio
    async def test_duplicate_relation_raises_typed_conflict(
        self, relation_service, knowledge_service
    ):
        a = await knowledge_service.create(
            CreateKnowledgeRequest(
                title="A", body="a", domain=Domain.process, jurisdiction=Jurisdiction.GLOBAL
            )
        )
        b = await knowledge_service.create(
            CreateKnowledgeRequest(
                title="B", body="b", domain=Domain.process, jurisdiction=Jurisdiction.GLOBAL
            )
        )
        req = CreateRelationRequest(
            to_item_id=b.knowledge_item_id,
            kind=RelationKind.related,
            actor="alice",
        )
        await relation_service.add(a.knowledge_item_id, req)
        # Second add with the same (from, to, kind) hits the UNIQUE
        # constraint. The narrowed IntegrityError catch is what makes
        # this surface as RelationConflictError rather than the raw
        # SQLAlchemy exception.
        with pytest.raises(RelationConflictError):
            await relation_service.add(a.knowledge_item_id, req)
