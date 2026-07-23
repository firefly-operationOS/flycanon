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

"""Coverage for the agent-tier replace + delete source endpoints.

* ``PUT /api/v1/agent/sources/{source_id}``    -- scope
  ``agent.sources:ingest``, replay dedup under
  ``agent.sources:replace``.
* ``DELETE /api/v1/agent/sources/{source_id}`` -- scope
  ``agent.sources:ingest``, replay dedup under
  ``agent.sources:delete``.

Each endpoint is covered by a happy path, the missing
``Idempotency-Key`` rejection, the replay short-circuit, the
scope refusal, and the unknown-id 404. The controllers are driven
directly with stub :class:`Request` objects and mocked CQRS buses,
identical to ``test_agent_surface`` / ``test_agent_idempotency_replay``.
The delete purge path is additionally pinned at the service layer:
``IntakeService.remove`` must wipe the index projections, the chunk
rows, and the source row, in that order.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pyfly.cqrs.exceptions import CommandProcessingException

from flycanon.core.services.auth.agent_token_service import (
    AgentScopeDenied,
    AgentTokenService,
    MintRequest,
)
from flycanon.core.services.sources import RemoveSourceCommand, ReplaceSourceCommand
from flycanon.core.services.sources.errors import SourceNotFound
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.interfaces.dtos.source import SourceRecord
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.source import SourceRow
from flycanon.web.controllers.agent.sources_controller import AgentSourcesController
from flycanon.web.controllers.sources_controller import SubmitSourceJsonPayload
from flycanon.web.conventions import InMemoryIdempotencyStore, MissingIdempotencyKey
from flycanon.web.conventions.exceptions import SourceNotFound as SourceNotFoundProblem

# ---------------------------------------------------------------------
# Test doubles (mirroring test_agent_surface)
# ---------------------------------------------------------------------


class _InMemoryAgentTokenRepository:
    """Mirror of the production repository's contract."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def insert(self, row: dict) -> None:
        self._rows[row["id"]] = row

    async def get_by_prefix(self, prefix: str, *, tenant_id: str) -> dict | None:
        for row in self._rows.values():
            if row["prefix"] == prefix and row["tenant_id"] == tenant_id and row["revoked_at"] is None:
                return row
        return None

    async def list_for_tenant(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def revoke(self, token_id: str, *, tenant_id: str, at: datetime) -> bool:
        row = self._rows.get(token_id)
        if not row or row["tenant_id"] != tenant_id or row["revoked_at"] is not None:
            return False
        row["revoked_at"] = at
        return True

    async def mark_used(self, token_id: str, *, tenant_id: str, at: datetime) -> None:
        row = self._rows.get(token_id)
        if row is not None and row["tenant_id"] == tenant_id:
            row["last_used_at"] = at


class _StubRequest:
    """Starlette-compatible Request stub with a headers dict."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


_TENANT = "acme"
_WORKSPACE = "ws-agent-test"


@pytest.fixture
def agent_token_service() -> AgentTokenService:
    return AgentTokenService(_InMemoryAgentTokenRepository())


async def _mint(service: AgentTokenService, *, scopes: list[str]) -> str:
    minted = await service.mint(
        MintRequest(
            tenant_id=_TENANT,
            name="test-token",
            workspace_allowlist=None,
            scopes=scopes,
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="anonymous",
    )
    return minted.token


def _request(token: str | None = None, *, idempotency_key: str | None = None) -> _StubRequest:
    headers: dict[str, str] = {
        "X-Tenant-Id": _TENANT,
        "X-Workspace-Id": _WORKSPACE,
    }
    if token is not None:
        headers["X-Agent-Token"] = token
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return _StubRequest(headers)


def _command_bus(send_returns: Any = None) -> AsyncMock:
    bus = AsyncMock()
    bus.send = AsyncMock(return_value=send_returns)
    return bus


def _sources_controller(
    agent_token_service: AgentTokenService,
    *,
    commands: Any = None,
    idempotency_store: Any = None,
) -> AgentSourcesController:
    return AgentSourcesController(
        agent_token_service=agent_token_service,
        commands=commands or _command_bus(),
        queries=AsyncMock(),
        url_fetcher=AsyncMock(),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
    )


def _submit_source_payload() -> SubmitSourceJsonPayload:
    return SubmitSourceJsonPayload(
        content_base64=base64.b64encode(b"hi-canon-v2").decode("ascii"),
        filename="hello.txt",
        content_type="text/plain",
    )


def _source_record(rid: str = "src-1") -> SourceRecord:
    now = datetime.now(UTC)
    return SourceRecord(
        id=rid,
        kind=SourceKind.text,
        status=SourceStatus.ingested,
        filename="hello.txt",
        uri=None,
        content_sha256="0" * 64,
        content_bytes=11,
        n_chunks=1,
        error_code=None,
        error_message=None,
        created_at=now,
        ingested_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------
# PUT /api/v1/agent/sources/{source_id}
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_happy_path_dispatches_replace_command(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus(send_returns=_source_record("src-1"))
    controller = _sources_controller(agent_token_service, commands=commands)

    result = await controller.replace(
        _request(token, idempotency_key="rep-key-1"),
        "src-1",
        _submit_source_payload(),
    )

    assert result.id == "src-1"
    commands.send.assert_awaited_once()
    cmd = commands.send.await_args.args[0]
    assert isinstance(cmd, ReplaceSourceCommand)
    assert cmd.source_id == "src-1"
    assert cmd.content == b"hi-canon-v2"
    assert cmd.tenant_id == _TENANT
    assert cmd.workspace_id == _WORKSPACE
    assert cmd.actor is not None
    assert cmd.actor.startswith("agent:")


@pytest.mark.asyncio
async def test_replace_missing_idempotency_key_returns_400(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    controller = _sources_controller(agent_token_service)
    with pytest.raises(MissingIdempotencyKey):
        await controller.replace(
            _request(token),  # no Idempotency-Key
            "src-1",
            _submit_source_payload(),
        )


@pytest.mark.asyncio
async def test_replace_same_key_returns_cached_response_without_redispatch(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus(send_returns=_source_record("src-replay-1"))
    controller = _sources_controller(agent_token_service, commands=commands)

    first = await controller.replace(
        _request(token, idempotency_key="rep-K1"),
        "src-replay-1",
        _submit_source_payload(),
    )
    second = await controller.replace(
        _request(token, idempotency_key="rep-K1"),
        "src-replay-1",
        _submit_source_payload(),
    )

    commands.send.assert_awaited_once()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert second.id == "src-replay-1"


@pytest.mark.asyncio
async def test_replace_wrong_scope_returns_403(agent_token_service: AgentTokenService) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:read"])
    controller = _sources_controller(agent_token_service)
    with pytest.raises(AgentScopeDenied):
        await controller.replace(
            _request(token, idempotency_key="key"),
            "src-1",
            _submit_source_payload(),
        )


@pytest.mark.asyncio
async def test_replace_unknown_id_maps_to_404_source_not_found(
    agent_token_service: AgentTokenService,
) -> None:
    """The production bus wraps ``SourceNotFound`` -- the controller must unwrap it.

    ``DefaultCommandBus`` never lets a handler error through raw: it
    re-raises as ``CommandProcessingException(cause=exc)``. The
    controller has to translate that back to the conventions
    ``SourceNotFound`` (``404 source_not_found``) or the caller would
    see a 500 ``command_processing_error``.
    """
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus()
    cause = SourceNotFound("src-missing")
    commands.send = AsyncMock(
        side_effect=CommandProcessingException(
            "Failed to process command ReplaceSourceCommand",
            cause=cause,
        )
    )
    controller = _sources_controller(agent_token_service, commands=commands)
    with pytest.raises(SourceNotFoundProblem) as excinfo:
        await controller.replace(
            _request(token, idempotency_key="key"),
            "src-missing",
            _submit_source_payload(),
        )
    assert excinfo.value.status == 404
    assert excinfo.value.code == "source_not_found"


@pytest.mark.asyncio
async def test_replace_wrapped_unrelated_cause_is_reraised(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus()
    commands.send = AsyncMock(
        side_effect=CommandProcessingException(
            "Failed to process command ReplaceSourceCommand",
            cause=RuntimeError("boom"),
        )
    )
    controller = _sources_controller(agent_token_service, commands=commands)
    with pytest.raises(CommandProcessingException):
        await controller.replace(
            _request(token, idempotency_key="key"),
            "src-missing",
            _submit_source_payload(),
        )


@pytest.mark.asyncio
async def test_replace_requires_content_base64(agent_token_service: AgentTokenService) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus()
    controller = _sources_controller(agent_token_service, commands=commands)
    payload = SubmitSourceJsonPayload(uri="https://example.com/doc.pdf")
    with pytest.raises(ValueError, match="content_base64 is required"):
        await controller.replace(
            _request(token, idempotency_key="key"),
            "src-1",
            payload,
        )
    commands.send.assert_not_awaited()


# ---------------------------------------------------------------------
# DELETE /api/v1/agent/sources/{source_id}
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_happy_path_dispatches_remove_command(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus()
    controller = _sources_controller(agent_token_service, commands=commands)

    result = await controller.remove(_request(token, idempotency_key="del-key-1"), "src-1")

    assert result is None
    commands.send.assert_awaited_once()
    cmd = commands.send.await_args.args[0]
    assert isinstance(cmd, RemoveSourceCommand)
    assert cmd.source_id == "src-1"
    assert cmd.tenant_id == _TENANT
    assert cmd.workspace_id == _WORKSPACE
    assert cmd.actor is not None
    assert cmd.actor.startswith("agent:")


@pytest.mark.asyncio
async def test_remove_missing_idempotency_key_returns_400(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    controller = _sources_controller(agent_token_service)
    with pytest.raises(MissingIdempotencyKey):
        await controller.remove(_request(token), "src-1")


@pytest.mark.asyncio
async def test_remove_same_key_short_circuits_without_redispatch(
    agent_token_service: AgentTokenService,
) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus()
    controller = _sources_controller(agent_token_service, commands=commands)

    await controller.remove(_request(token, idempotency_key="del-K1"), "src-1")
    await controller.remove(_request(token, idempotency_key="del-K1"), "src-1")

    # The bus was hit exactly once across the two calls -- the retry
    # replays the original 204 instead of surfacing a spurious 404.
    commands.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_wrong_scope_returns_403(agent_token_service: AgentTokenService) -> None:
    token = await _mint(agent_token_service, scopes=["agent.sources:read"])
    controller = _sources_controller(agent_token_service)
    with pytest.raises(AgentScopeDenied):
        await controller.remove(_request(token, idempotency_key="key"), "src-1")


@pytest.mark.asyncio
async def test_remove_unknown_id_maps_to_404_source_not_found(
    agent_token_service: AgentTokenService,
) -> None:
    """Same unwrap contract as the PUT: wrapped ``SourceNotFound`` -> 404."""
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus()
    cause = SourceNotFound("src-missing")
    commands.send = AsyncMock(
        side_effect=CommandProcessingException(
            "Failed to process command RemoveSourceCommand",
            cause=cause,
        )
    )
    controller = _sources_controller(agent_token_service, commands=commands)
    with pytest.raises(SourceNotFoundProblem) as excinfo:
        await controller.remove(_request(token, idempotency_key="key"), "src-missing")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "source_not_found"


@pytest.mark.asyncio
async def test_replace_and_delete_same_key_do_not_collide(
    agent_token_service: AgentTokenService,
) -> None:
    # The PUT + DELETE replay caches are namespaced by route-specific
    # dedup scopes -- one key reused across the two verbs must trigger
    # a dispatch for each.
    token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
    commands = _command_bus(send_returns=_source_record("src-1"))
    store = InMemoryIdempotencyStore()
    controller = _sources_controller(agent_token_service, commands=commands, idempotency_store=store)

    await controller.replace(
        _request(token, idempotency_key="shared-K"),
        "src-1",
        _submit_source_payload(),
    )
    await controller.remove(_request(token, idempotency_key="shared-K"), "src-1")

    assert commands.send.await_count == 2


# ---------------------------------------------------------------------
# IntakeService.remove -- the purge path
# ---------------------------------------------------------------------


def _existing_row() -> SourceRow:
    return SourceRow(
        id="src-existing",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        kind=SourceKind.text.value,
        status=SourceStatus.ingested.value,
        filename="note.txt",
        uri=None,
        content_type="text/plain",
        content_sha256="oldsha",
        content_bytes=5,
        n_chunks=1,
        metadata_json={},
    )


def _make_remove_intake(
    *,
    existing: SourceRow | None,
) -> tuple[IntakeService, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Wire an IntakeService with the collaborators the remove path touches."""
    indexer = MagicMock()
    indexer.remove_for_source = AsyncMock(return_value=1)

    sources = MagicMock()
    sources.get = AsyncMock(return_value=existing)
    sources.delete = AsyncMock(return_value=None)

    chunk_repository = MagicMock()
    chunk_repository.replace_for_source = AsyncMock(return_value=0)

    audit = MagicMock()
    audit.record = AsyncMock(return_value=None)

    event_publisher = MagicMock()
    event_publisher.publish = AsyncMock(return_value=None)

    settings = MagicMock()
    settings.pii_policy = "disabled"
    settings.pii_scanner = "noop"
    settings.ingest_topic = "flycanon.ingest"
    settings.store_originals = False

    service = IntakeService(
        binary_normalizer=MagicMock(),
        ingestion=MagicMock(),
        loaders=MagicMock(),
        embeddings=MagicMock(),
        indexer=indexer,
        metadata_extractor=MagicMock(),
        source_repository=sources,
        chunk_repository=chunk_repository,
        audit=audit,
        event_publisher=event_publisher,
        object_store=MagicMock(),
        settings=settings,
    )
    return service, indexer, sources, chunk_repository, audit, event_publisher


@pytest.mark.asyncio
async def test_intake_remove_purges_index_chunks_and_row() -> None:
    existing = _existing_row()
    service, indexer, sources, chunks, audit, publisher = _make_remove_intake(existing=existing)

    await service.remove(
        source_id="src-existing",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        actor="agent:abc",
        correlation_id="corr-1",
    )

    # Dense vectors + BM25 rows purged under the caller's scope.
    indexer.remove_for_source.assert_awaited_once_with(
        "src-existing",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
    )
    # Chunk rows deleted, then the source row itself.
    chunks.replace_for_source.assert_awaited_once_with("src-existing", [])
    sources.delete.assert_awaited_once_with(existing)
    # Audit trail + EDA event mirror SourceIngested/SourceReplaced.
    audit.record.assert_awaited_once()
    assert audit.record.await_args.kwargs["event_type"] == "source.removed"
    publisher.publish.assert_awaited_once()
    assert publisher.publish.await_args.kwargs["event_type"] == "SourceRemoved"


@pytest.mark.asyncio
async def test_intake_remove_purges_vectors_before_deleting_chunk_rows() -> None:
    # remove_for_source resolves the source's chunk ids through the
    # chunk repository; deleting the rows first would leak the dense
    # vectors. Pin the ordering.
    existing = _existing_row()
    service, indexer, _, chunks, _, _ = _make_remove_intake(existing=existing)
    order: list[str] = []
    indexer.remove_for_source = AsyncMock(side_effect=lambda *a, **k: order.append("index"))
    chunks.replace_for_source = AsyncMock(side_effect=lambda *a, **k: order.append("chunks"))

    await service.remove(
        source_id="src-existing",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
    )

    assert order == ["index", "chunks"]


@pytest.mark.asyncio
async def test_intake_remove_unknown_id_raises_source_not_found() -> None:
    service, indexer, sources, chunks, _, _ = _make_remove_intake(existing=None)

    with pytest.raises(SourceNotFound):
        await service.remove(
            source_id="src-missing",
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
        )

    indexer.remove_for_source.assert_not_awaited()
    chunks.replace_for_source.assert_not_awaited()
    sources.delete.assert_not_awaited()
