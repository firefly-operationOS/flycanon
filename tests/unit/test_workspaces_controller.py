# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""End-to-end coverage for the ``/api/v1/workspaces`` controller.

Exercises the controller methods directly with a stubbed Starlette
:class:`Request` so the full handler chain (TenantContext extraction
-> repository call -> DTO conversion) is verified without spinning up
the pyfly DI graph. Mirrors how the other controller-flavoured unit
tests interact with their services.

Coverage matches the workspace CRUD contract:

* ``POST``           -> 201 + :class:`WorkspaceSpec`.
* ``GET ""``         -> list of :class:`WorkspaceSummary` filtered by tenant.
* ``GET /{id}``      -> :class:`WorkspaceSpec` or 404 ``workspace_not_found``.
* ``PATCH /{id}``    -> sparse update returns the refreshed :class:`WorkspaceSpec`.
* ``POST /{id}:close`` -> transitions ``status -> closed`` + sets ``closed_at``.
* Duplicate id ``POST`` raises ``IntegrityError`` (caught upstream as a 409).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from flycanon.config import CanonSettings
from flycanon.core.services.events import WorkspaceEventPublisher
from flycanon.interfaces.dtos.workspace import (
    WorkspaceCreate,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
)
from flycanon.interfaces.dtos.workspace_event import (
    WorkspaceCreated,
    WorkspaceDeleted,
    WorkspaceEventBase,
    WorkspaceUpdated,
)
from flycanon.interfaces.enums.workspace_status import WorkspaceStatus
from flycanon.models.repositories.workspace_repository import WorkspaceRepository
from flycanon.web.controllers.workspaces_controller import WorkspacesController
from flycanon.web.conventions import WorkspaceNotFound


class _NullPublisher:
    """No-op pyfly :class:`EventPublisher` stub for unit tests.

    The controller's lifecycle paths each call into
    :class:`WorkspaceEventPublisher`, which in production forwards to
    pyfly's bus. We don't exercise the bus in these tests -- the
    in-process listener fan-out (see ``_recording_publisher``) is
    enough to assert what each route emits.
    """

    async def publish(self, **_: Any) -> None:
        return None

    def subscribe(self, *_: Any, **__: Any) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.fixture
def workspace_repo(engine, session_factory) -> WorkspaceRepository:
    return WorkspaceRepository(session_factory, engine=engine)


@pytest.fixture
def captured_events() -> list[WorkspaceEventBase]:
    return []


@pytest.fixture
def event_publisher(
    captured_events: list[WorkspaceEventBase],
) -> WorkspaceEventPublisher:
    pub = WorkspaceEventPublisher(
        event_publisher=_NullPublisher(),
        settings=CanonSettings(),
    )

    async def _listener(event: WorkspaceEventBase) -> None:
        captured_events.append(event)

    pub.add_listener(_listener)
    return pub


class _RecordingIntake:
    """Stand-in for :class:`IntakeService` -- records removals and deletes the row.

    The purge service must drive every source through the real removal
    pipeline (vectors, chunks, original, row, audit, event). Here the
    pipeline is the unit under test's collaborator, so the stub only
    proves the service called it once per source with the right scope
    and mimics the one side effect the service relies on -- the row
    disappearing -- by deleting it through the repository.
    """

    def __init__(self, source_repo) -> None:
        self._sources = source_repo
        self.removed: list[tuple[str, str, str]] = []

    async def remove(self, *, source_id, tenant_id, workspace_id, actor=None, correlation_id=None):
        self.removed.append((source_id, tenant_id, workspace_id))
        row = await self._sources.get(source_id, tenant_id=tenant_id, workspace_id=workspace_id)
        assert row is not None
        await self._sources.delete(row)


class _RecordingAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


@pytest.fixture
def purge_parts(engine, session_factory, workspace_repo, event_publisher):
    from flycanon.core.services.workspaces import WorkspacePurgeService
    from flycanon.models.repositories.scope_purge_repository import ScopePurgeRepository
    from flycanon.models.repositories.source_repository import SourceRepository

    source_repo = SourceRepository(session_factory, engine=engine)
    intake = _RecordingIntake(source_repo)
    audit = _RecordingAudit()
    service = WorkspacePurgeService(
        intake=intake,  # type: ignore[arg-type]
        source_repository=source_repo,
        purge_repository=ScopePurgeRepository(session_factory, engine=engine),
        workspace_repository=workspace_repo,
        audit=audit,  # type: ignore[arg-type]
        event_publisher=event_publisher,
    )
    return SimpleNamespace(service=service, intake=intake, audit=audit, source_repo=source_repo)


@pytest.fixture
def controller(
    workspace_repo: WorkspaceRepository,
    event_publisher: WorkspaceEventPublisher,
    purge_parts,
) -> WorkspacesController:
    return WorkspacesController(workspace_repo, event_publisher, purge_parts.service)


def _request(tenant_id: str = "acme", workspace_id: str = "default") -> object:
    """Build a Starlette-compatible request stub.

    ``tenant_context_from_request`` reads ``request.headers.get(...)``;
    a SimpleNamespace with a ``headers`` dict-like is enough to feed
    the convention helper without a full Starlette Request.
    """
    headers = {
        "X-Tenant-Id": tenant_id,
        "X-Workspace-Id": workspace_id,
    }
    return SimpleNamespace(headers=headers)


class TestCreate:
    @pytest.mark.asyncio
    async def test_post_creates_and_returns_spec(self, controller: WorkspacesController) -> None:
        body = WorkspaceCreate(
            id="ws-1",
            name="Q3 audit",
            status=WorkspaceStatus.active,
        )
        spec = await controller.create(_request(tenant_id="acme"), body)
        assert isinstance(spec, WorkspaceSpec)
        assert spec.id == "ws-1"
        assert spec.tenant_id == "acme"
        assert spec.name == "Q3 audit"
        assert spec.status == WorkspaceStatus.active
        assert spec.created_at is not None
        assert spec.updated_at is not None
        assert spec.closed_at is None

    @pytest.mark.asyncio
    async def test_duplicate_id_raises_integrity_error(self, controller: WorkspacesController) -> None:
        body = WorkspaceCreate(id="ws-dup", name="A", status=WorkspaceStatus.active)
        await controller.create(_request(tenant_id="acme"), body)
        with pytest.raises(IntegrityError):
            await controller.create(_request(tenant_id="acme"), body)


class TestList:
    @pytest.mark.asyncio
    async def test_list_returns_tenant_workspaces(self, controller: WorkspacesController) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-acme-1", name="A", status=WorkspaceStatus.active),
        )
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-acme-2", name="B", status=WorkspaceStatus.active),
        )
        # Different tenant row -- must NOT surface in acme's list. The
        # workspace id PK is globally unique so the bcorp row uses a
        # distinct id; the partitioning happens at the tenant_id WHERE
        # clause in :meth:`list_for_tenant`, not at id collision.
        await controller.create(
            _request(tenant_id="bcorp"),
            WorkspaceCreate(id="ws-bcorp-1", name="cross-tenant", status=WorkspaceStatus.active),
        )
        rows = await controller.list_workspaces(_request(tenant_id="acme"))
        assert all(isinstance(r, WorkspaceSummary) for r in rows)
        ids = {r.id for r in rows}
        # Only the two acme rows surface; the bcorp row is filtered out.
        assert ids == {"ws-acme-1", "ws-acme-2"}
        assert {r.tenant_id for r in rows} == {"acme"}


class TestGet:
    @pytest.mark.asyncio
    async def test_get_returns_spec(self, controller: WorkspacesController) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        spec = await controller.get(_request(tenant_id="acme"), "ws-1")
        assert isinstance(spec, WorkspaceSpec)
        assert spec.id == "ws-1"
        assert spec.tenant_id == "acme"

    @pytest.mark.asyncio
    async def test_get_unknown_raises_404(self, controller: WorkspacesController) -> None:
        with pytest.raises(WorkspaceNotFound):
            await controller.get(_request(tenant_id="acme"), "ws-missing")

    @pytest.mark.asyncio
    async def test_get_cross_tenant_raises_404(self, controller: WorkspacesController) -> None:
        # Workspace lives under tenant=acme; bcorp must not see it.
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        with pytest.raises(WorkspaceNotFound):
            await controller.get(_request(tenant_id="bcorp"), "ws-1")


class TestUpdate:
    @pytest.mark.asyncio
    async def test_patch_applies_sparse_update(self, controller: WorkspacesController) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="Original", status=WorkspaceStatus.active),
        )
        updated = await controller.update(
            _request(tenant_id="acme"),
            "ws-1",
            WorkspaceUpdate(name="Renamed"),
        )
        assert isinstance(updated, WorkspaceSpec)
        assert updated.name == "Renamed"
        assert updated.status == WorkspaceStatus.active

    @pytest.mark.asyncio
    async def test_patch_unknown_raises_404(self, controller: WorkspacesController) -> None:
        with pytest.raises(WorkspaceNotFound):
            await controller.update(
                _request(tenant_id="acme"),
                "ws-missing",
                WorkspaceUpdate(name="x"),
            )


class TestClose:
    @pytest.mark.asyncio
    async def test_close_transitions_status_and_stamps_closed_at(
        self, controller: WorkspacesController
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        closed = await controller.close(_request(tenant_id="acme"), "ws-1")
        assert isinstance(closed, WorkspaceSpec)
        assert closed.status == WorkspaceStatus.closed
        assert closed.closed_at is not None

    @pytest.mark.asyncio
    async def test_close_unknown_raises_404(self, controller: WorkspacesController) -> None:
        with pytest.raises(WorkspaceNotFound):
            await controller.close(_request(tenant_id="acme"), "ws-missing")


class TestLifecycleEvents:
    """Each mutation route emits the matching workspace lifecycle event.

    The fixture-attached listener captures every event the publisher
    fans out -- production code goes through the same code path,
    additionally pushing onto pyfly's bus (stubbed out as a no-op
    here).
    """

    @pytest.mark.asyncio
    async def test_post_emits_workspace_created(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="Q3 audit", status=WorkspaceStatus.active),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert isinstance(event, WorkspaceCreated)
        assert event.event_type == "workspace.created"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.name == "Q3 audit"
        assert event.occurred_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_patch_emits_workspace_updated(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="Original", status=WorkspaceStatus.active),
        )
        captured_events.clear()  # drop the WorkspaceCreated from create()
        await controller.update(
            _request(tenant_id="acme"),
            "ws-1",
            WorkspaceUpdate(name="Renamed"),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert isinstance(event, WorkspaceUpdated)
        assert event.event_type == "workspace.updated"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"
        assert event.name == "Renamed"

    @pytest.mark.asyncio
    async def test_close_emits_workspace_deleted(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        await controller.create(
            _request(tenant_id="acme"),
            WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active),
        )
        captured_events.clear()
        await controller.close(_request(tenant_id="acme"), "ws-1")
        assert len(captured_events) == 1
        event = captured_events[0]
        assert isinstance(event, WorkspaceDeleted)
        assert event.event_type == "workspace.deleted"
        assert event.tenant_id == "acme"
        assert event.workspace_id == "ws-1"

    @pytest.mark.asyncio
    async def test_failed_create_publishes_no_event(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """Best-effort consistency: event only fires after a successful repo write.

        Duplicate-id ``POST`` raises ``IntegrityError`` *before* the
        controller reaches the publish call -- the listener must
        not see a phantom WorkspaceCreated.
        """
        body = WorkspaceCreate(id="ws-1", name="A", status=WorkspaceStatus.active)
        await controller.create(_request(tenant_id="acme"), body)
        captured_events.clear()
        with pytest.raises(IntegrityError):
            await controller.create(_request(tenant_id="acme"), body)
        assert captured_events == []

    @pytest.mark.asyncio
    async def test_failed_update_publishes_no_event(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """A PATCH against a missing workspace must not publish."""
        with pytest.raises(WorkspaceNotFound):
            await controller.update(
                _request(tenant_id="acme"),
                "ws-missing",
                WorkspaceUpdate(name="x"),
            )
        assert captured_events == []

    @pytest.mark.asyncio
    async def test_failed_close_publishes_no_event(
        self,
        controller: WorkspacesController,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """A ``:close`` against a missing workspace must not publish."""
        with pytest.raises(WorkspaceNotFound):
            await controller.close(_request(tenant_id="acme"), "ws-missing")
        assert captured_events == []


# ---------------------------------------------------------------------------
# POST /{id}:purge (26.7.1)
# ---------------------------------------------------------------------------


async def _seed_scope(session_factory, *, tenant_id: str, workspace_id: str, suffix: str) -> None:
    """Populate one scope with a row in every table the purge sweeps."""
    from flycanon.models.entities.candidate import CandidateRow
    from flycanon.models.entities.conversation import ConversationRow, ConversationTurnRow
    from flycanon.models.entities.cost_event import CostEventRow
    from flycanon.models.entities.ingest_job import IngestJobEventRow, IngestJobRow
    from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
    from flycanon.models.entities.knowledge_item import KnowledgeItemRow
    from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
    from flycanon.models.entities.source import SourceRow

    scope = {"tenant_id": tenant_id, "workspace_id": workspace_id}
    async with session_factory() as session:
        session.add(
            SourceRow(
                id=f"src-{suffix}",
                kind="markdown",
                status="ingested",
                filename="f.md",
                content_type="text/markdown",
                content_sha256=suffix.ljust(64, "0"),
                content_bytes=10,
                n_chunks=1,
                metadata_json={},
                object_store_key=f"flycanon/{tenant_id}/{workspace_id}/sources/src-{suffix}.md",
                **scope,
            )
        )
        session.add(
            KnowledgeChunkRow(
                id=f"chunk-{suffix}",
                source_id=f"src-{suffix}",
                index_in_source=0,
                total_chunks=1,
                content="hello",
                **scope,
            )
        )
        session.add(
            KnowledgeItemRow(
                id=f"ki-{suffix}",
                status="published",
                current_version=1,
                title="t",
                domain="legal",
                jurisdiction="ES",
                tags_json=[],
                **scope,
            )
        )
        session.add(
            KnowledgeVersionRow(
                id=f"kv-{suffix}",
                knowledge_item_id=f"ki-{suffix}",
                version=1,
                status="published",
                title="t",
                summary="s",
                body="b",
                domain="legal",
                jurisdiction="ES",
                tags_json=[],
                **scope,
            )
        )
        session.add(
            CandidateRow(
                id=f"cand-{suffix}",
                status="proposed",
                source_id=f"src-{suffix}",
                title="c",
                summary="s",
                body="b",
                domain="legal",
                jurisdiction="ES",
                tags_json=[],
                citations_json=[],
                score=0.5,
                rationale=None,
                actor=None,
                **scope,
            )
        )
        session.add(
            ConversationRow(
                id=f"conv-{suffix}",
                title="conv",
                actor=None,
                model="anthropic:claude-sonnet-4-6",
                metadata_json={},
                **scope,
            )
        )
        session.add(
            ConversationTurnRow(
                conversation_id=f"conv-{suffix}",
                turn_index=0,
                question="q",
                answer="a",
                citations_json=[],
                model="m",
                elapsed_ms=1,
                no_answer=False,
                **scope,
            )
        )
        session.add(
            IngestJobRow(
                id=f"job-{suffix}",
                status="succeeded",
                source_id=f"src-{suffix}",
                attempts=1,
                filename="f.md",
                content_type="text/markdown",
                uri=None,
                content_sha256=None,
                actor=None,
                correlation_id=None,
                callback_url=None,
                metadata_json={},
                error_code=None,
                error_message=None,
                **scope,
            )
        )
        session.add(
            IngestJobEventRow(
                job_id=f"job-{suffix}",
                stage="queued",
                message="m",
                payload_json={},
                **scope,
            )
        )
        session.add(
            CostEventRow(
                agent_name="flycanon-rlm-answerer",
                model="m",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
                subject_kind="answer",
                subject_id=f"conv-{suffix}",
                **scope,
            )
        )
        await session.commit()


async def _count_rows(session_factory, mapped, *, tenant_id: str, workspace_id: str) -> int:
    from sqlalchemy import func, select

    async with session_factory() as session:
        stmt = (
            select(func.count())
            .select_from(mapped)
            .where(mapped.tenant_id == tenant_id, mapped.workspace_id == workspace_id)
        )
        return int((await session.execute(stmt)).scalar() or 0)


class TestPurge:
    @pytest.mark.asyncio
    async def test_purge_erases_every_scoped_table_and_closes(
        self,
        controller: WorkspacesController,
        purge_parts,
        session_factory,
        captured_events: list[WorkspaceEventBase],
    ) -> None:
        """Every table in the scope is emptied, the neighbour scope is untouched."""
        from flycanon.models.entities.candidate import CandidateRow
        from flycanon.models.entities.conversation import ConversationRow
        from flycanon.models.entities.knowledge_item import KnowledgeItemRow
        from flycanon.models.entities.source import SourceRow

        await controller.create(
            _request(tenant_id="acme", workspace_id="ws-a"),
            WorkspaceCreate(id="ws-a", name="A", status=WorkspaceStatus.active),
        )
        await _seed_scope(session_factory, tenant_id="acme", workspace_id="ws-a", suffix="a1")
        await _seed_scope(session_factory, tenant_id="acme", workspace_id="ws-b", suffix="b1")
        captured_events.clear()

        result = await controller.purge(_request(tenant_id="acme", workspace_id="ws-a"), "ws-a")

        assert result.tenant_id == "acme" and result.workspace_id == "ws-a"
        assert result.sources_removed == 1
        assert result.originals_deleted == 1
        assert result.knowledge_items_removed == 1
        assert result.knowledge_versions_removed == 1
        assert result.candidates_removed == 1
        assert result.conversations_removed == 1
        assert result.conversation_turns_removed == 1
        assert result.ingest_jobs_removed == 1
        assert result.ingest_job_events_removed == 1
        assert result.cost_events_removed == 1
        # The chunk was NOT removed by the stubbed intake, so the sweep
        # must have caught it -- proving the sweep covers orphaned chunks.
        assert result.chunks_removed == 1
        assert result.closed is True

        # Sources went through the removal pipeline with the right scope.
        assert purge_parts.intake.removed == [("src-a1", "acme", "ws-a")]
        # The neighbour workspace of the same tenant is intact.
        for mapped in (SourceRow, KnowledgeItemRow, CandidateRow, ConversationRow):
            assert await _count_rows(session_factory, mapped, tenant_id="acme", workspace_id="ws-a") == 0
            assert await _count_rows(session_factory, mapped, tenant_id="acme", workspace_id="ws-b") == 1
        # Closed + lifecycle event + audit trail.
        row = await controller.get(_request(tenant_id="acme", workspace_id="ws-a"), "ws-a")
        assert row.status == WorkspaceStatus.closed and row.closed_at is not None
        assert [type(e) for e in captured_events] == [WorkspaceDeleted]
        audit = purge_parts.audit.records[-1]
        assert audit["event_type"] == "workspace.purged"
        assert audit["payload"]["sources_removed"] == 1 and audit["payload"]["closed"] is True

    @pytest.mark.asyncio
    async def test_purge_is_idempotent_and_tolerates_implicit_workspace(
        self,
        controller: WorkspacesController,
        session_factory,
        purge_parts,
    ) -> None:
        """No canon_workspaces row: data is still purged, ``closed`` is False, and a repeat is all zeros."""
        await _seed_scope(session_factory, tenant_id="acme", workspace_id="ws-implicit", suffix="i1")

        first = await controller.purge(_request(tenant_id="acme", workspace_id="ws-implicit"), "ws-implicit")
        assert first.sources_removed == 1 and first.closed is False

        second = await controller.purge(_request(tenant_id="acme", workspace_id="ws-implicit"), "ws-implicit")
        assert second.sources_removed == 0
        assert second.chunks_removed == 0 and second.knowledge_items_removed == 0
        assert second.closed is False
        assert len(purge_parts.intake.removed) == 1

    @pytest.mark.asyncio
    async def test_purge_refuses_header_path_disagreement(
        self,
        controller: WorkspacesController,
        session_factory,
        purge_parts,
    ) -> None:
        """A path id that differs from X-Workspace-Id is a 400, and nothing is touched."""
        from flycanon.web.conventions import WorkspaceScopeMismatch

        await _seed_scope(session_factory, tenant_id="acme", workspace_id="ws-b", suffix="b2")
        with pytest.raises(WorkspaceScopeMismatch) as exc_info:
            await controller.purge(_request(tenant_id="acme", workspace_id="ws-a"), "ws-b")
        assert exc_info.value.code == "workspace_scope_mismatch"
        assert purge_parts.intake.removed == []
        from flycanon.models.entities.source import SourceRow

        assert await _count_rows(session_factory, SourceRow, tenant_id="acme", workspace_id="ws-b") == 1
