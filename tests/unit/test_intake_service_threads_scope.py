# Copyright 2026 Firefly Software Solutions Inc
"""Scope threading coverage for :class:`IntakeService` -> :class:`IndexService`.

This pins the Plan 6 write-path hole that was silently writing every
ingested chunk vector to the ``('default', 'default')`` scope: intake
called ``IndexService.replace_for_source`` without scope kwargs, so the
``replace_for_source`` soft-default kicked in and every vector landed in
the wrong RLS scope -- invisible to the real tenant/workspace under the
migration 0013 policies.

The submit + replace paths MUST thread ``tenant_id`` / ``workspace_id``
from the caller into the indexer. The index-service signature, in turn,
removes the ``'default'`` fallback (belt-and-braces): callers that forget
now fail loud rather than silently corrupting the scope.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fireflyframework_agentic.content.binary import BinaryArtifact

from flycanon.config import CanonSettings
from flycanon.core.services.ingestion import IngestionResult
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.interfaces.dtos.source import SourceMetadata, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.source import SourceRow


def _artifact() -> BinaryArtifact:
    return BinaryArtifact(
        bytes=b"hello world",
        media_type="text/plain",
        kind=SourceKind.text,
        filename="note.txt",
    )


def _chunk(source_id: str, *, tenant_id: str, workspace_id: str) -> KnowledgeChunkRow:
    return KnowledgeChunkRow(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_id=source_id,
        index_in_source=0,
        total_chunks=1,
        content="hello",
        char_start=0,
        char_end=5,
        page=None,
        section_path=None,
        metadata_json={},
    )


def _settings() -> CanonSettings:
    """Minimal settings stub -- PII scanner disabled, EDA topics declared."""
    s = MagicMock(spec=CanonSettings)
    s.pii_policy = "disabled"
    s.pii_scanner = "noop"
    s.ingest_topic = "flycanon.ingest"
    s.source_ingested_event = "SourceIngested"
    s.source_ingestion_failed_event = "SourceIngestionFailed"
    return s


def _make_intake(
    *,
    indexer: Any,
    sources_existing: SourceRow | None = None,
    chunks: list[KnowledgeChunkRow] | None = None,
) -> tuple[IntakeService, MagicMock]:
    """Wire up an IntakeService with mocked dependencies.

    Returns the service + the SourceRepository mock so callers can
    inspect / configure persistence behaviour.
    """
    binary_normalizer = MagicMock()
    binary_normalizer.normalise = AsyncMock(return_value=[_artifact()])

    ingestion = MagicMock()

    # The ingestion service produces the in-memory SourceRow + chunks
    # synchronously -- intake_service.submit calls it as a plain func.
    def _do_ingest(
        *, source: SourceRow, content: bytes, tenant_id: str, workspace_id: str
    ) -> IngestionResult:
        source.content_sha256 = "abc123"
        source.content_bytes = len(content)
        source.n_chunks = len(chunks or [])
        source.status = SourceStatus.ingested.value
        return IngestionResult(source=source, chunks=list(chunks or []))

    ingestion.ingest = _do_ingest

    loaders = MagicMock()
    loaders.get = MagicMock(return_value=None)

    embeddings = MagicMock()
    embeddings.model = "fake-embedder"
    embeddings.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

    metadata_extractor = MagicMock()
    extracted = MagicMock()
    extracted.to_dict = MagicMock(return_value={})
    metadata_extractor.extract = MagicMock(return_value=extracted)

    sources = MagicMock()
    sources.add = AsyncMock(return_value=None)
    sources.update = AsyncMock(side_effect=lambda row: row)
    sources.get_by_content_sha256 = AsyncMock(return_value=None)
    sources.get = AsyncMock(return_value=sources_existing)

    chunk_repository = MagicMock()
    chunk_repository.replace_for_source = AsyncMock(return_value=None)

    audit = MagicMock()
    audit.record = AsyncMock(return_value=None)

    event_publisher = MagicMock()
    event_publisher.publish = AsyncMock(return_value=None)

    service = IntakeService(
        binary_normalizer=binary_normalizer,
        ingestion=ingestion,
        loaders=loaders,
        embeddings=embeddings,
        indexer=indexer,
        metadata_extractor=metadata_extractor,
        source_repository=sources,
        chunk_repository=chunk_repository,
        audit=audit,
        event_publisher=event_publisher,
        settings=_settings(),
    )
    return service, sources


@pytest.fixture
def request_dto() -> SubmitSourceRequest:
    return SubmitSourceRequest(
        kind=SourceKind.text,
        uri=None,
        metadata=SourceMetadata(),
    )


class TestIntakeSubmitThreadsScopeToIndexer:
    @pytest.mark.asyncio
    async def test_submit_writes_chunks_under_caller_scope(self, request_dto: SubmitSourceRequest) -> None:
        # The submit path MUST forward tenant_id / workspace_id into
        # IndexService.replace_for_source so the vector row lands in
        # the caller's RLS scope (not the silent ('default','default')
        # fallback).
        indexer = MagicMock()
        indexer.replace_for_source = AsyncMock(return_value=1)

        # The chunks the ingestion produces; intake then passes them
        # to the indexer.
        chunks = [_chunk("placeholder", tenant_id="acme", workspace_id="ws-A")]
        service, _ = _make_intake(indexer=indexer, chunks=chunks)

        await service.submit(
            request=request_dto,
            content=b"hello",
            tenant_id="acme",
            workspace_id="ws-A",
            filename="note.txt",
            content_type="text/plain",
        )

        # Indexer saw the caller's scope, NOT the legacy 'default'.
        indexer.replace_for_source.assert_awaited_once()
        kwargs = indexer.replace_for_source.await_args.kwargs
        assert kwargs["tenant_id"] == "acme", (
            f"expected tenant_id='acme', got {kwargs.get('tenant_id')!r} -- "
            "intake silently fell back to the 'default' scope, undermining Plan 6 RLS."
        )
        assert kwargs["workspace_id"] == "ws-A", (
            f"expected workspace_id='ws-A', got {kwargs.get('workspace_id')!r} -- "
            "intake silently fell back to the 'default' scope, undermining Plan 6 RLS."
        )


class TestIntakeReplaceThreadsScopeToIndexer:
    @pytest.mark.asyncio
    async def test_replace_writes_chunks_under_caller_scope(self, request_dto: SubmitSourceRequest) -> None:
        # Same scope-threading guarantee on the PUT/replace path: the
        # re-ingest flow must forward the caller's scope to the
        # indexer instead of silently defaulting.
        indexer = MagicMock()
        indexer.replace_for_source = AsyncMock(return_value=1)

        existing = SourceRow(
            id="src-existing",
            tenant_id="acme",
            workspace_id="ws-A",
            kind=SourceKind.text.value,
            status=SourceStatus.ingested.value,
            filename="note.txt",
            uri=None,
            content_type="text/plain",
            content_sha256="oldsha",
            content_bytes=0,
            n_chunks=0,
            metadata_json={},
        )
        chunks = [_chunk("src-existing", tenant_id="acme", workspace_id="ws-A")]
        service, _ = _make_intake(indexer=indexer, sources_existing=existing, chunks=chunks)

        await service.replace(
            source_id="src-existing",
            request=request_dto,
            content=b"hello again",
            tenant_id="acme",
            workspace_id="ws-A",
            filename="note.txt",
            content_type="text/plain",
        )

        indexer.replace_for_source.assert_awaited_once()
        kwargs = indexer.replace_for_source.await_args.kwargs
        assert kwargs["tenant_id"] == "acme"
        assert kwargs["workspace_id"] == "ws-A"


class TestIndexServiceScopeKwargsRequired:
    def test_replace_for_source_has_no_default_for_scope(self) -> None:
        # Belt-and-braces: a forgotten kwarg on the write path must
        # surface as a TypeError at call time, not silently land in
        # the ('default', 'default') RLS bucket.
        import inspect

        from flycanon.core.services.retrieval.index_service import IndexService

        sig = inspect.signature(IndexService.replace_for_source)
        params = sig.parameters
        assert "tenant_id" in params
        assert "workspace_id" in params
        assert params["tenant_id"].default is inspect.Parameter.empty, (
            "IndexService.replace_for_source must not default tenant_id -- "
            "forgotten scope kwargs silently corrupted the RLS write path."
        )
        assert params["workspace_id"].default is inspect.Parameter.empty, (
            "IndexService.replace_for_source must not default workspace_id -- "
            "forgotten scope kwargs silently corrupted the RLS write path."
        )
