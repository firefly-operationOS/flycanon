# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`IngestJobRepository`."""

from __future__ import annotations

import pytest

from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository


@pytest.fixture
def jobs_repo(repositories, engine, session_factory):
    return IngestJobRepository(session_factory, engine=engine)


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_add_then_get_round_trip(self, jobs_repo):
        row = await jobs_repo.add(
            IngestJobRow(
                id="job-1",
                status="queued",
                filename="x.pdf",
                metadata_json={"k": "v"},
            )
        )
        assert row.id == "job-1"
        fetched = await jobs_repo.get("job-1")
        assert fetched is not None
        assert fetched.status == "queued"

    @pytest.mark.asyncio
    async def test_mark_running_increments_attempts_and_sets_started_at(self, jobs_repo):
        await jobs_repo.add(IngestJobRow(id="job-1", status="queued"))
        row = await jobs_repo.mark_running("job-1")
        assert row is not None
        assert row.status == "running"
        assert row.attempts == 1
        assert row.started_at is not None

    @pytest.mark.asyncio
    async def test_mark_succeeded_records_terminal_state(self, jobs_repo):
        await jobs_repo.add(IngestJobRow(id="job-1", status="queued"))
        await jobs_repo.mark_running("job-1")
        row = await jobs_repo.mark_succeeded("job-1", source_id="src-1", content_sha256="abc")
        assert row is not None
        assert row.status == "succeeded"
        assert row.source_id == "src-1"
        assert row.content_sha256 == "abc"
        assert row.finished_at is not None

    @pytest.mark.asyncio
    async def test_mark_failed_records_typed_error(self, jobs_repo):
        await jobs_repo.add(IngestJobRow(id="job-1", status="queued"))
        await jobs_repo.mark_running("job-1")
        row = await jobs_repo.mark_failed("job-1", code="boom", message="something went wrong")
        assert row is not None
        assert row.status == "failed"
        assert row.error_code == "boom"
        assert row.error_message == "something went wrong"


class TestEvents:
    @pytest.mark.asyncio
    async def test_append_and_list_events_in_order(self, jobs_repo):
        await jobs_repo.add(IngestJobRow(id="job-1", status="queued"))
        await jobs_repo.append_event("job-1", stage="queued", message="m1")
        await jobs_repo.append_event("job-1", stage="loading", message="m2")
        await jobs_repo.append_event("job-1", stage="finished", payload={"source_id": "s"})
        events = await jobs_repo.list_events("job-1")
        assert [e.stage for e in events] == ["queued", "loading", "finished"]
        # Ids monotonic.
        assert events[0].id < events[1].id < events[2].id

    @pytest.mark.asyncio
    async def test_after_id_cursor_returns_only_new_events(self, jobs_repo):
        await jobs_repo.add(IngestJobRow(id="job-1", status="queued"))
        e1 = await jobs_repo.append_event("job-1", stage="queued")
        e2 = await jobs_repo.append_event("job-1", stage="loading")
        # cursor = e1.id -- only e2 should come back.
        events = await jobs_repo.list_events("job-1", after_id=e1.id)
        assert [e.id for e in events] == [e2.id]
