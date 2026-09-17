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

"""26.7.1 multi-tenant hardening -- the source and ingest side.

Pins, in one place, the behaviours a multi-tenant control plane relies
on and that were missing or unsafe before this release:

* ``DELETE /api/v1/sources/{id}`` exists on the user tier and maps the
  unknown-id case to ``404 source_not_found``.
* ``IntakeService.remove`` deletes the stored original from the object
  store (and tolerates a missing object).
* Every ``flycanon.ingest`` payload carries ``tenant_id`` /
  ``workspace_id`` (sync intake, replace, remove, async requested,
  async finished / failed).
* ``callback_url`` is vetted by the host policy at submit time.
* The async-ingest webhook is signed with ``X-Flycanon-Signature``
  over the exact bytes sent, carries the scope, and goes unsigned only
  when no secret is configured.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from pyfly.cqrs.exceptions import CommandProcessingException

from flycanon.config import CanonSettings
from flycanon.core.services.sources import RemoveSourceCommand
from flycanon.core.services.sources.async_ingest_service import AsyncIngestService
from flycanon.core.services.sources.errors import SourceNotFound as ServiceSourceNotFound
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.core.services.sources.url_fetcher import UrlFetcher
from flycanon.interfaces.dtos.source import SourceMetadata, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.entities.source import SourceRow
from flycanon.web.controllers.sources_controller import SourcesController, SubmitSourceJsonPayload
from flycanon.web.conventions.exceptions import CallbackUrlNotAllowed, SourceNotFound
from flycanon.web.conventions.headers import HEADER_WEBHOOK_SIGNATURE
from flycanon.web.conventions.webhook_signature import verify_signature

_TENANT = "acme"
_WORKSPACE = "ws-1"
_PUBLIC_IP = "93.184.216.34"


def _request(headers: dict[str, str] | None = None) -> Any:
    base = {"X-Tenant-Id": _TENANT, "X-Workspace-Id": _WORKSPACE}
    base.update(headers or {})
    return SimpleNamespace(headers=base)


def _resolver(host: str) -> list[str]:
    return ["10.0.0.7"] if host == "internal.example.com" else [_PUBLIC_IP]


def _fetcher(**overrides: Any) -> UrlFetcher:
    settings = SimpleNamespace(
        max_bytes=1024, url_fetch_timeout_s=5.0, url_fetch_allow_private=False, **overrides
    )
    return UrlFetcher(settings, resolver=_resolver)


def _controller(commands: Any = None, async_ingest: Any = None) -> SourcesController:
    return SourcesController(
        commands=commands or MagicMock(),
        queries=MagicMock(),
        url_fetcher=_fetcher(),
        async_ingest=async_ingest or MagicMock(),
    )


# ---------------------------------------------------------------------
# DELETE /api/v1/sources/{id}
# ---------------------------------------------------------------------


class TestUserTierDelete:
    @pytest.mark.asyncio
    async def test_dispatches_remove_command_with_request_scope(self) -> None:
        commands = MagicMock()
        commands.send = AsyncMock(return_value=None)
        controller = _controller(commands=commands)

        result = await controller.remove_source(_request(), "src-1")

        assert result is None
        command = commands.send.await_args.args[0]
        assert isinstance(command, RemoveSourceCommand)
        assert (command.source_id, command.tenant_id, command.workspace_id) == ("src-1", _TENANT, _WORKSPACE)

    @pytest.mark.asyncio
    async def test_unknown_id_renders_404_source_not_found(self) -> None:
        commands = MagicMock()
        wrapped = CommandProcessingException("boom", cause=ServiceSourceNotFound("src-x"))
        commands.send = AsyncMock(side_effect=wrapped)
        controller = _controller(commands=commands)

        with pytest.raises(SourceNotFound) as exc_info:
            await controller.remove_source(_request(), "src-x")
        assert exc_info.value.status == 404 and exc_info.value.code == "source_not_found"

    @pytest.mark.asyncio
    async def test_other_handler_errors_propagate(self) -> None:
        commands = MagicMock()
        commands.send = AsyncMock(side_effect=CommandProcessingException("boom", cause=RuntimeError("db")))
        with pytest.raises(CommandProcessingException):
            await _controller(commands=commands).remove_source(_request(), "src-1")


# ---------------------------------------------------------------------
# callback_url vetting on POST /api/v1/sources?mode=async
# ---------------------------------------------------------------------


class TestCallbackUrlVetting:
    def _payload(self) -> SubmitSourceJsonPayload:
        return SubmitSourceJsonPayload(content_base64="aGVsbG8=", filename="a.txt")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "callback",
        [
            "http://127.0.0.1:8080/hook",
            "http://169.254.169.254/",
            "http://cp-web/hook",
            "https://internal.example.com/hook",
            "ftp://hooks.example.com/",
        ],
    )
    async def test_forbidden_callback_is_refused_before_queueing(self, callback: str) -> None:
        async_ingest = MagicMock()
        async_ingest.submit_async = AsyncMock()
        controller = _controller(async_ingest=async_ingest)

        with pytest.raises(CallbackUrlNotAllowed) as exc_info:
            await controller.submit_json(_request(), self._payload(), mode="async", callback_url=callback)
        assert exc_info.value.status == 400 and exc_info.value.code == "callback_url_not_allowed"
        async_ingest.submit_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_public_callback_is_accepted_and_forwarded(self) -> None:
        async_ingest = MagicMock()
        now = datetime.now(UTC)
        row = IngestJobRow(
            id="job-1",
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            status="queued",
            attempts=0,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        async_ingest.submit_async = AsyncMock(return_value=row)
        controller = _controller(async_ingest=async_ingest)

        await controller.submit_json(
            _request(), self._payload(), mode="async", callback_url="https://hooks.example.com/flycanon"
        )
        assert (
            async_ingest.submit_async.await_args.kwargs["callback_url"]
            == "https://hooks.example.com/flycanon"
        )


# ---------------------------------------------------------------------
# IntakeService.remove deletes the original + scoped payloads
# ---------------------------------------------------------------------


def _row(*, object_store_key: str | None) -> SourceRow:
    return SourceRow(
        id="src-1",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        kind=SourceKind.text.value,
        status=SourceStatus.ingested.value,
        filename="note.txt",
        content_type="text/plain",
        content_sha256="sha",
        content_bytes=5,
        n_chunks=1,
        metadata_json={},
        object_store_key=object_store_key,
    )


def _intake(existing: SourceRow, *, object_store: Any) -> tuple[IntakeService, MagicMock, MagicMock]:
    sources = MagicMock()
    sources.get = AsyncMock(return_value=existing)
    sources.delete = AsyncMock(return_value=None)
    indexer = MagicMock()
    indexer.remove_for_source = AsyncMock(return_value=1)
    chunks = MagicMock()
    chunks.replace_for_source = AsyncMock(return_value=0)
    audit = MagicMock()
    audit.record = AsyncMock(return_value=None)
    publisher = MagicMock()
    publisher.publish = AsyncMock(return_value=None)
    settings = CanonSettings(pii_policy="disabled", store_originals=True)
    service = IntakeService(
        binary_normalizer=MagicMock(),
        ingestion=MagicMock(),
        loaders=MagicMock(),
        embeddings=MagicMock(),
        indexer=indexer,
        metadata_extractor=MagicMock(),
        source_repository=sources,
        chunk_repository=chunks,
        audit=audit,
        event_publisher=publisher,
        object_store=object_store,
        settings=settings,
    )
    return service, audit, publisher


class TestRemoveDeletesOriginal:
    @pytest.mark.asyncio
    async def test_original_deleted_and_reported(self) -> None:
        store = MagicMock()
        store.delete = AsyncMock(return_value=None)
        key = f"flycanon/{_TENANT}/{_WORKSPACE}/sources/src-1.txt"
        service, audit, publisher = _intake(_row(object_store_key=key), object_store=store)

        await service.remove(source_id="src-1", tenant_id=_TENANT, workspace_id=_WORKSPACE)

        store.delete.assert_awaited_once_with(key)
        assert audit.record.await_args.kwargs["payload"]["original_deleted"] is True
        payload = publisher.publish.await_args.kwargs["payload"]
        assert payload["original_deleted"] is True
        assert (payload["tenant_id"], payload["workspace_id"]) == (_TENANT, _WORKSPACE)

    @pytest.mark.asyncio
    async def test_missing_object_is_tolerated(self) -> None:
        store = MagicMock()
        store.delete = AsyncMock(side_effect=FileNotFoundError("gone"))
        service, audit, _ = _intake(_row(object_store_key="k"), object_store=store)

        await service.remove(source_id="src-1", tenant_id=_TENANT, workspace_id=_WORKSPACE)

        assert audit.record.await_args.kwargs["payload"]["original_deleted"] is False

    @pytest.mark.asyncio
    async def test_row_without_key_skips_the_store(self) -> None:
        store = MagicMock()
        store.delete = AsyncMock()
        service, audit, _ = _intake(_row(object_store_key=None), object_store=store)

        await service.remove(source_id="src-1", tenant_id=_TENANT, workspace_id=_WORKSPACE)

        store.delete.assert_not_awaited()
        assert audit.record.await_args.kwargs["payload"]["original_deleted"] is False

    @pytest.mark.asyncio
    async def test_store_failure_keeps_the_row_for_retry(self) -> None:
        # A hard store error must surface BEFORE the row is deleted so
        # the source still points at its object and a retry can finish
        # the job -- an orphaned object nobody references is the worse
        # outcome.
        store = MagicMock()
        store.delete = AsyncMock(side_effect=OSError("bucket unreachable"))
        service, _, _ = _intake(_row(object_store_key="k"), object_store=store)
        sources = service._sources  # type: ignore[attr-defined]

        with pytest.raises(OSError):
            await service.remove(source_id="src-1", tenant_id=_TENANT, workspace_id=_WORKSPACE)
        sources.delete.assert_not_awaited()


class TestIngestTopicPayloadsCarryScope:
    @pytest.mark.asyncio
    async def test_success_and_failure_publishes_include_scope(self) -> None:
        service, _, publisher = _intake(_row(object_store_key=None), object_store=MagicMock())
        row = _row(object_store_key=None)

        await service._publish_success(source=row, correlation_id="c1")  # type: ignore[attr-defined]
        await service._publish_failure(source=row, exc=RuntimeError("x"), correlation_id="c1")  # type: ignore[attr-defined]

        for call in publisher.publish.await_args_list:
            payload = call.kwargs["payload"]
            assert payload["tenant_id"] == _TENANT and payload["workspace_id"] == _WORKSPACE
        assert [c.kwargs["event_type"] for c in publisher.publish.await_args_list] == [
            "SourceIngested",
            "SourceIngestionFailed",
        ]


# ---------------------------------------------------------------------
# Async ingest: scoped events + signed webhook
# ---------------------------------------------------------------------


def _async_service(settings: CanonSettings) -> tuple[AsyncIngestService, MagicMock, MagicMock]:
    repository = MagicMock()
    publisher = MagicMock()
    publisher.publish = AsyncMock(return_value=None)
    audit = MagicMock()
    audit.record = AsyncMock(return_value=None)
    service = AsyncIngestService(
        intake=MagicMock(),
        repository=repository,
        audit=audit,
        event_publisher=publisher,
        settings=settings,
    )
    return service, repository, publisher


def _job(callback_url: str | None) -> IngestJobRow:
    return IngestJobRow(
        id="job-1",
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        status="running",
        attempts=1,
        correlation_id="corr-1",
        callback_url=callback_url,
        metadata_json={},
    )


class TestAsyncIngestScopeAndWebhook:
    @pytest.mark.asyncio
    async def test_requested_event_carries_scope(self) -> None:
        service, repository, publisher = _async_service(CanonSettings())
        stored = _job(None)
        stored.status = "queued"
        repository.add = AsyncMock(return_value=stored)
        repository.append_event = AsyncMock(return_value=None)

        await service.submit_async(
            request=SubmitSourceRequest(kind=SourceKind.text, metadata=SourceMetadata()),
            content=b"hello",
            filename="a.txt",
            content_type="text/plain",
            actor=None,
            correlation_id="corr-1",
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
        )
        payload = publisher.publish.await_args.kwargs["payload"]
        assert payload == {"job_id": "job-1", "tenant_id": _TENANT, "workspace_id": _WORKSPACE}

    @pytest.mark.asyncio
    @respx.mock
    async def test_webhook_is_signed_over_the_exact_bytes_and_carries_scope(self, respx_mock) -> None:
        secret = "whsec_unit"
        service, _, _ = _async_service(CanonSettings(webhook_secret=secret))
        route = respx_mock.post("https://hooks.example.com/flycanon").mock(return_value=httpx.Response(200))

        await service._fire_webhook(  # type: ignore[attr-defined]
            _job("https://hooks.example.com/flycanon"), status="succeeded", source_id="src-9"
        )

        assert route.called
        sent = route.calls.last.request
        header = sent.headers.get(HEADER_WEBHOOK_SIGNATURE)
        assert header and header.startswith("t=")
        # The receiver verifies against the raw bytes it received.
        assert verify_signature(secret, sent.content, header) is True
        assert verify_signature("wrong", sent.content, header) is False
        body = json.loads(sent.content)
        assert body["tenant_id"] == _TENANT and body["workspace_id"] == _WORKSPACE
        assert body["job_id"] == "job-1" and body["status"] == "succeeded" and body["source_id"] == "src-9"
        assert sent.headers["X-Correlation-Id"] == "corr-1"
        assert sent.headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_webhook_unsigned_when_no_secret(self, respx_mock) -> None:
        service, _, _ = _async_service(CanonSettings(webhook_secret=""))
        route = respx_mock.post("https://hooks.example.com/flycanon").mock(return_value=httpx.Response(200))

        await service._fire_webhook(
            _job("https://hooks.example.com/flycanon"), status="failed", error_code="x"
        )  # type: ignore[attr-defined]

        assert HEADER_WEBHOOK_SIGNATURE not in route.calls.last.request.headers

    @pytest.mark.asyncio
    async def test_no_callback_means_no_request(self) -> None:
        service, _, _ = _async_service(CanonSettings(webhook_secret="s"))
        with respx.mock(assert_all_called=False) as router:
            await service._fire_webhook(_job(None), status="succeeded")  # type: ignore[attr-defined]
            assert not router.calls
