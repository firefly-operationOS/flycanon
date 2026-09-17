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

"""26.7.1 multi-tenant hardening -- conversations, job stream, workspaces, image.

* ``ConversationService`` answers through the :class:`AnswerDispatcher`
  (the same engine as ``POST /api/v1/query``) and the turn route emits
  the RAG deprecation header only when RAG is selected.
* ``GET /api/v1/ingest-jobs/{id}/stream`` resumes from ``?after_id=`` or
  ``Last-Event-ID`` and stamps ``id:`` on every event frame.
* ``WorkspaceRepository`` runs ``list_for_tenant`` on the admin engine
  when ``FLYCANON_ADMIN_DATABASE_URL`` names a different DSN.
* The shipped image defaults the localfs object-store root to the
  writable state directory and pre-creates ``/app/var``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.responses import JSONResponse

from flycanon.config import CanonSettings
from flycanon.core.services.conversations import ConversationService
from flycanon.interfaces.dtos.conversation import CreateTurnRequest
from flycanon.interfaces.dtos.job import IngestJob, IngestJobEvent
from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse
from flycanon.models.entities.conversation import ConversationRow, ConversationTurnRow
from flycanon.models.repositories.workspace_repository import WorkspaceRepository
from flycanon.web.controllers.conversations_controller import ConversationsController
from flycanon.web.controllers.jobs_controller import JobsController, _sse_event, resolve_resume_cursor
from flycanon.web.conventions.headers import DEPRECATION_RAG_MESSAGE, HEADER_DEPRECATION

_TENANT = "acme"
_WORKSPACE = "ws-1"


def _request(headers: dict[str, str] | None = None) -> Any:
    base = {"X-Tenant-Id": _TENANT, "X-Workspace-Id": _WORKSPACE}
    base.update(headers or {})
    return SimpleNamespace(headers=base)


# ---------------------------------------------------------------------
# Conversations on the AnswerDispatcher
# ---------------------------------------------------------------------


class _FakeDispatcher:
    def __init__(self, *, is_rag: bool) -> None:
        self.is_rag = is_rag
        self.mode = "rag" if is_rag else "rlm"
        self.calls: list[dict[str, Any]] = []

    async def answer(
        self, request: AnswerRequest, *, prior_turns=None, tenant_id=None, workspace_id=None, on_turn=None
    ):
        self.calls.append(
            {
                "request": request,
                "prior_turns": prior_turns,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )
        return AnswerResponse(answer="42", citations=[], model="rlm:test", elapsed_ms=3, no_answer=False)


def _conversation_row() -> ConversationRow:
    now = datetime.now(UTC)
    return ConversationRow(
        id="conv-1",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        title="t",
        actor="user:1",
        model="anthropic:claude-sonnet-4-6",
        metadata_json={},
        created_at=now,
        updated_at=now,
    )


def _turn_row(index: int) -> ConversationTurnRow:
    return ConversationTurnRow(
        id=index + 1,
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        conversation_id="conv-1",
        turn_index=index,
        question=f"q{index}",
        answer=f"a{index}",
        citations_json=[],
        model="rlm:test",
        elapsed_ms=1,
        no_answer=False,
        created_at=datetime.now(UTC),
    )


def _conversation_service(dispatcher: _FakeDispatcher) -> tuple[ConversationService, MagicMock]:
    repository = MagicMock()
    repository.get = AsyncMock(return_value=_conversation_row())
    repository.list_turns = AsyncMock(return_value=[_turn_row(0), _turn_row(1)])
    repository.next_turn_index = AsyncMock(return_value=2)

    async def _add_turn(row: ConversationTurnRow) -> ConversationTurnRow:
        row.id = row.id or 3
        row.created_at = row.created_at or datetime.now(UTC)
        return row

    repository.add_turn = AsyncMock(side_effect=_add_turn)
    audit = MagicMock()
    audit.record = AsyncMock(return_value=None)
    service = ConversationService(
        repository=repository,
        answer_dispatcher=dispatcher,  # type: ignore[arg-type]
        audit=audit,
        settings=CanonSettings(),
    )
    return service, repository


class TestConversationOnDispatcher:
    @pytest.mark.asyncio
    async def test_turn_answers_through_the_dispatcher_with_scope_and_history(self) -> None:
        dispatcher = _FakeDispatcher(is_rag=False)
        service, _ = _conversation_service(dispatcher)

        _conv, turn = await service.append_turn(
            "conv-1",
            CreateTurnRequest(question="and then?", top_k=4),
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
        )

        assert turn.answer == "42" and turn.model == "rlm:test"
        assert len(dispatcher.calls) == 1
        call = dispatcher.calls[0]
        assert (call["tenant_id"], call["workspace_id"]) == (_TENANT, _WORKSPACE)
        # The two most recent turns ride on message_history.
        assert call["prior_turns"] == [("q0", "a0"), ("q1", "a1")]
        assert call["request"].question == "and then?"
        assert "Prior conversation summary" in (call["request"].instructions or "")
        assert service.is_rag is False

    @pytest.mark.asyncio
    async def test_turn_route_adds_deprecation_header_only_for_rag(self) -> None:
        for is_rag in (False, True):
            dispatcher = _FakeDispatcher(is_rag=is_rag)
            service, _ = _conversation_service(dispatcher)
            controller = ConversationsController(MagicMock(), MagicMock(), service)

            response = await controller.append_turn(_request(), "conv-1", CreateTurnRequest(question="q"))

            if is_rag:
                assert isinstance(response, JSONResponse)
                assert response.status_code == 201
                assert response.headers[HEADER_DEPRECATION] == DEPRECATION_RAG_MESSAGE
            else:
                assert not isinstance(response, JSONResponse)
                assert response.turn.answer == "42"


# ---------------------------------------------------------------------
# Job stream resume
# ---------------------------------------------------------------------


class TestResumeCursor:
    def test_query_param_wins(self) -> None:
        assert resolve_resume_cursor(7, "3") == 7

    def test_last_event_id_header_used_when_no_param(self) -> None:
        assert resolve_resume_cursor(0, "12") == 12
        assert resolve_resume_cursor(0, " 12 ") == 12

    @pytest.mark.parametrize("header", [None, "", "abc", "0", "-4"])
    def test_absent_or_invalid_means_from_the_beginning(self, header: str | None) -> None:
        assert resolve_resume_cursor(0, header) is None
        assert resolve_resume_cursor(-1, header) is None

    def test_event_frames_carry_an_id_line(self) -> None:
        frame = _sse_event("event", {"id": 5}, event_id=5).decode()
        assert frame == 'event: event\nid: 5\ndata: {"id": 5}\n\n'
        assert "id:" not in _sse_event("status", {"status": "queued"}).decode()


def _job_dto(status: str) -> IngestJob:
    now = datetime.now(UTC)
    return IngestJob(
        id="job-1",
        status=status,  # type: ignore[arg-type]
        attempts=1,
        created_at=now,
        updated_at=now,
    )


def _event_dto(event_id: int) -> IngestJobEvent:
    return IngestJobEvent(
        id=event_id,
        job_id="job-1",
        stage="loading",
        message=f"e{event_id}",
        payload={},
        occurred_at=datetime.now(UTC),
    )


class _QueryBus:
    """Answers GetIngestJobQuery / ListIngestJobEventsQuery from canned data and records cursors."""

    def __init__(self, events: list[IngestJobEvent]) -> None:
        self._events = events
        self.after_ids: list[int | None] = []

    async def query(self, query: Any) -> Any:
        name = type(query).__name__
        if name == "GetIngestJobQuery":
            return _job_dto("succeeded")
        if name == "ListIngestJobEventsQuery":
            self.after_ids.append(query.after_id)
            return [e for e in self._events if query.after_id is None or e.id > query.after_id]
        raise AssertionError(name)


async def _drain(response: Any) -> str:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks).decode()


class TestJobStreamResume:
    @pytest.mark.asyncio
    async def test_after_id_skips_already_seen_events(self) -> None:
        bus = _QueryBus([_event_dto(1), _event_dto(2), _event_dto(3)])
        controller = JobsController(bus)  # type: ignore[arg-type]

        response = await controller.stream_job(_request(), "job-1", poll_interval_ms=50, after_id=2)
        body = await _drain(response)

        assert bus.after_ids[0] == 2
        assert "id: 3\n" in body and "id: 1\n" not in body and "id: 2\n" not in body
        assert 'event: status\ndata: {"id": "job-1", "status": "succeeded"' in body

    @pytest.mark.asyncio
    async def test_last_event_id_header_resumes_too(self) -> None:
        bus = _QueryBus([_event_dto(1), _event_dto(2)])
        controller = JobsController(bus)  # type: ignore[arg-type]

        response = await controller.stream_job(_request({"Last-Event-ID": "1"}), "job-1", poll_interval_ms=50)
        body = await _drain(response)

        assert bus.after_ids[0] == 1
        assert "id: 2\n" in body and "id: 1\n" not in body

    @pytest.mark.asyncio
    async def test_default_replays_from_the_beginning(self) -> None:
        bus = _QueryBus([_event_dto(1), _event_dto(2)])
        controller = JobsController(bus)  # type: ignore[arg-type]

        body = await _drain(await controller.stream_job(_request(), "job-1", poll_interval_ms=50))

        assert bus.after_ids[0] is None
        assert "id: 1\n" in body and "id: 2\n" in body


# ---------------------------------------------------------------------
# WorkspaceRepository admin engine
# ---------------------------------------------------------------------


class TestWorkspaceAdminEngine:
    def test_same_dsn_means_no_admin_engine(self) -> None:
        repo = WorkspaceRepository.from_urls(
            "sqlite+aiosqlite:///:memory:", admin_database_url="sqlite+aiosqlite:///:memory:"
        )
        assert repo.has_admin_engine is False

    def test_distinct_dsn_builds_an_admin_engine(self, tmp_path: Path) -> None:
        repo = WorkspaceRepository.from_urls(
            "sqlite+aiosqlite:///:memory:",
            admin_database_url=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
        )
        assert repo.has_admin_engine is True

    def test_settings_fall_back_to_the_request_dsn(self) -> None:
        settings = CanonSettings(database_url="postgresql+asyncpg://app@db/x", admin_database_url="  ")
        assert settings.effective_admin_database_url == "postgresql+asyncpg://app@db/x"
        settings = CanonSettings(
            database_url="postgresql+asyncpg://app@db/x",
            admin_database_url="postgresql+asyncpg://admin@db/x",
        )
        assert settings.effective_admin_database_url == "postgresql+asyncpg://admin@db/x"

    @pytest.mark.asyncio
    async def test_list_for_tenant_reads_through_the_admin_factory(self, engine, session_factory) -> None:
        """The listing must go through the admin factory, the composite reads through the request one."""
        calls: list[str] = []

        class _Counting:
            def __init__(self, label: str) -> None:
                self._label = label

            def __call__(self):
                calls.append(self._label)
                return session_factory()

        repo = WorkspaceRepository(
            _Counting("request"),  # type: ignore[arg-type]
            engine=engine,
            admin_session_factory=_Counting("admin"),  # type: ignore[arg-type]
        )
        await repo.insert({"id": "ws-1", "tenant_id": "acme", "name": "A", "status": "active"})
        assert (await repo.get("acme", "ws-1")) is not None
        listed = await repo.list_for_tenant("acme")

        assert [w["id"] for w in listed] == ["ws-1"]
        assert calls == ["request", "request", "admin"]


# ---------------------------------------------------------------------
# Image contract
# ---------------------------------------------------------------------


class TestImageObjectStoreDefault:
    """The Dockerfile must make the localfs object store writable.

    ``FLYCANON_OBJECT_STORE_LOCALFS_ROOT`` defaults to ``./var/objects``
    -- fine from a checkout, but ``/app`` in the image is root-owned and
    the process runs as ``canon``, so the first ingest failed with
    EACCES and the container never served. The image therefore exports
    the writable state-dir root AND pre-creates ``/app/var`` for anyone
    who sets the variable back.
    """

    def test_dockerfile_exports_writable_root_and_creates_var(self) -> None:
        dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
        assert "FLYCANON_OBJECT_STORE_LOCALFS_ROOT=/app/canon-data/objects" in dockerfile
        assert "mkdir -p /app/canon-data/objects /app/var/objects" in dockerfile
        assert "chown -R canon:canon /app/canon-data /app/var" in dockerfile
        # The env line must precede the USER switch so it is baked for the runtime user.
        assert dockerfile.index("FLYCANON_OBJECT_STORE_LOCALFS_ROOT") < dockerfile.index("USER canon")
