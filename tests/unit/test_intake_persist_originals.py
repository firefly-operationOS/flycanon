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

"""Original-document persistence coverage for :class:`IntakeService`.

Pins the RLM intake contract: when ``FLYCANON_STORE_ORIGINALS`` is on,
submit + replace write the ORIGINAL uploaded bytes to the object store
under ``flycanon/{tenant}/{workspace}/sources/{source_id}{ext}`` and
stamp the key on the source row. When off, no write happens and the key
stays null. A store failure is best-effort: it logs and leaves the key
null without failing the ingest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fireflyframework_agentic.content.binary import BinaryArtifact

from flycanon.config import CanonSettings
from flycanon.core.services.ingestion import IngestionResult
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.core.services.storage.local_fs import LocalFsObjectStore
from flycanon.core.services.storage.object_store import ObjectStore
from flycanon.interfaces.dtos.source import SourceMetadata, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.source import SourceRow

ORIGINAL = b"the original uploaded document"


def _artifact() -> BinaryArtifact:
    # The merged content the pipeline produces differs from the
    # original bytes -- this lets the tests assert the ORIGINAL is what
    # gets stored, never the merged/normalised payload.
    return BinaryArtifact(
        bytes=b"merged-not-original",
        media_type="text/plain",
        kind=SourceKind.text,
        filename="note.txt",
    )


def _chunk() -> KnowledgeChunkRow:
    return KnowledgeChunkRow(
        tenant_id="acme",
        workspace_id="ws-A",
        source_id="placeholder",
        index_in_source=0,
        total_chunks=1,
        content="hello",
        char_start=0,
        char_end=5,
        page=None,
        section_path=None,
        metadata_json={},
    )


def _settings(*, store_originals: bool) -> CanonSettings:
    s = MagicMock(spec=CanonSettings)
    s.pii_policy = "disabled"
    s.pii_scanner = "noop"
    s.ingest_topic = "flycanon.ingest"
    s.source_ingested_event = "SourceIngested"
    s.source_ingestion_failed_event = "SourceIngestionFailed"
    s.store_originals = store_originals
    return s


def _make_intake(
    *,
    object_store: ObjectStore,
    store_originals: bool,
    sources_existing: SourceRow | None = None,
) -> tuple[IntakeService, MagicMock]:
    binary_normalizer = MagicMock()
    binary_normalizer.normalise = AsyncMock(return_value=[_artifact()])

    ingestion = MagicMock()

    def _do_ingest(
        *, source: SourceRow, content: bytes, tenant_id: str, workspace_id: str
    ) -> IngestionResult:
        source.content_sha256 = "abc123"
        source.content_bytes = len(content)
        source.n_chunks = 1
        source.status = SourceStatus.ingested.value
        return IngestionResult(source=source, chunks=[_chunk()])

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

    indexer = MagicMock()
    indexer.replace_for_source = AsyncMock(return_value=1)

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
        object_store=object_store,
        settings=_settings(store_originals=store_originals),
    )
    return service, sources


def _request() -> SubmitSourceRequest:
    return SubmitSourceRequest(kind=SourceKind.text, uri=None, metadata=SourceMetadata())


class _FailingObjectStore(ObjectStore):
    """ObjectStore whose put always raises -- exercises best-effort path."""

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        raise RuntimeError("backend down")

    async def get(self, key: str) -> bytes:  # pragma: no cover - unused
        raise FileNotFoundError(key)

    async def delete(self, key: str) -> None:  # pragma: no cover - unused
        return None

    async def exists(self, key: str) -> bool:  # pragma: no cover - unused
        return False


@pytest.mark.asyncio
async def test_submit_persists_original_and_sets_key(tmp_path: Path) -> None:
    store = LocalFsObjectStore(root=str(tmp_path))
    service, sources = _make_intake(object_store=store, store_originals=True)

    row = await service.submit(
        request=_request(),
        content=ORIGINAL,
        tenant_id="acme",
        workspace_id="ws-A",
        filename="note.txt",
        content_type="text/plain",
    )

    expected_key = f"flycanon/acme/ws-A/sources/{row.id}.txt"
    assert row.object_store_key == expected_key
    # The ORIGINAL bytes are stored, not the merged pipeline content.
    assert await store.get(expected_key) == ORIGINAL
    # The key lands on the same row that is inserted.
    sources.add.assert_awaited_once()
    assert sources.add.await_args.args[0].object_store_key == expected_key


@pytest.mark.asyncio
async def test_submit_skips_store_when_disabled(tmp_path: Path) -> None:
    store = MagicMock()
    store.put = AsyncMock(return_value=None)
    service, _ = _make_intake(object_store=store, store_originals=False)

    row = await service.submit(
        request=_request(),
        content=ORIGINAL,
        tenant_id="acme",
        workspace_id="ws-A",
        filename="note.txt",
        content_type="text/plain",
    )

    store.put.assert_not_awaited()
    assert row.object_store_key is None


@pytest.mark.asyncio
async def test_submit_survives_store_failure(tmp_path: Path) -> None:
    service, _ = _make_intake(object_store=_FailingObjectStore(), store_originals=True)

    row = await service.submit(
        request=_request(),
        content=ORIGINAL,
        tenant_id="acme",
        workspace_id="ws-A",
        filename="note.txt",
        content_type="text/plain",
    )

    # Ingest still succeeds; the key stays null on failure.
    assert row.object_store_key is None
    assert row.status == SourceStatus.ingested.value


@pytest.mark.asyncio
async def test_submit_extension_falls_back_to_content_type(tmp_path: Path) -> None:
    store = LocalFsObjectStore(root=str(tmp_path))
    service, _ = _make_intake(object_store=store, store_originals=True)

    row = await service.submit(
        request=_request(),
        content=ORIGINAL,
        tenant_id="acme",
        workspace_id="ws-A",
        filename=None,
        content_type="application/pdf",
    )

    assert row.object_store_key == f"flycanon/acme/ws-A/sources/{row.id}.pdf"


@pytest.mark.asyncio
async def test_submit_extension_falls_back_to_bin(tmp_path: Path) -> None:
    store = LocalFsObjectStore(root=str(tmp_path))
    service, _ = _make_intake(object_store=store, store_originals=True)

    row = await service.submit(
        request=_request(),
        content=ORIGINAL,
        tenant_id="acme",
        workspace_id="ws-A",
        filename=None,
        content_type=None,
    )

    assert row.object_store_key == f"flycanon/acme/ws-A/sources/{row.id}.bin"


def _existing_row() -> SourceRow:
    return SourceRow(
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


@pytest.mark.asyncio
async def test_replace_persists_new_original_and_sets_key(tmp_path: Path) -> None:
    store = LocalFsObjectStore(root=str(tmp_path))
    existing = _existing_row()
    service, sources = _make_intake(object_store=store, store_originals=True, sources_existing=existing)

    new_bytes = b"the replaced original document"
    row = await service.replace(
        source_id="src-existing",
        request=_request(),
        content=new_bytes,
        tenant_id="acme",
        workspace_id="ws-A",
        filename="note.txt",
        content_type="text/plain",
    )

    expected_key = "flycanon/acme/ws-A/sources/src-existing.txt"
    assert row.object_store_key == expected_key
    assert await store.get(expected_key) == new_bytes
    # The key is set on the existing row BEFORE the UPDATE.
    sources.update.assert_awaited_once()
    assert sources.update.await_args.args[0].object_store_key == expected_key


@pytest.mark.asyncio
async def test_replace_survives_store_failure(tmp_path: Path) -> None:
    existing = _existing_row()
    service, _ = _make_intake(
        object_store=_FailingObjectStore(), store_originals=True, sources_existing=existing
    )

    row: Any = await service.replace(
        source_id="src-existing",
        request=_request(),
        content=b"new",
        tenant_id="acme",
        workspace_id="ws-A",
        filename="note.txt",
        content_type="text/plain",
    )

    assert row.object_store_key is None


@pytest.mark.asyncio
async def test_replace_skips_store_when_disabled(tmp_path: Path) -> None:
    store = MagicMock()
    store.put = AsyncMock(return_value=None)
    existing = _existing_row()
    service, _ = _make_intake(object_store=store, store_originals=False, sources_existing=existing)

    row = await service.replace(
        source_id="src-existing",
        request=_request(),
        content=b"new",
        tenant_id="acme",
        workspace_id="ws-A",
        filename="note.txt",
        content_type="text/plain",
    )

    store.put.assert_not_awaited()
    assert row.object_store_key is None
