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

"""IngestionService glue -- loader + chunker -> chunk rows."""

from __future__ import annotations

import pytest

from flycanon.core.services.ingestion import IngestionService
from flycanon.core.services.ingestion.chunker import ParagraphChunker
from flycanon.core.services.ingestion.errors import EmptySource
from flycanon.core.services.ingestion.loaders import default_registry
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.source import SourceRow


def test_detect_kind_from_filename_extension():
    assert IngestionService.detect_kind(filename="doc.docx", content_type=None) == SourceKind.docx
    assert IngestionService.detect_kind(filename="doc.pdf", content_type=None) == SourceKind.pdf
    assert IngestionService.detect_kind(filename="doc.html", content_type=None) == SourceKind.html
    assert IngestionService.detect_kind(filename="doc.md", content_type=None) == SourceKind.markdown
    assert IngestionService.detect_kind(filename="doc.txt", content_type=None) == SourceKind.text
    assert IngestionService.detect_kind(filename="doc.bin", content_type=None) == SourceKind.unknown


def test_detect_kind_prefers_content_type():
    assert (
        IngestionService.detect_kind(
            filename="bytes",
            content_type="application/pdf",
        )
        == SourceKind.pdf
    )


def _service() -> IngestionService:
    return IngestionService(loaders=default_registry(), chunker=ParagraphChunker())


def test_ingest_markdown_emits_chunks_with_section_path(fixtures_dir):
    bytes_ = (fixtures_dir / "sample.md").read_bytes()
    source = SourceRow(
        id="src-1",
        tenant_id="default",
        workspace_id="default",
        kind=SourceKind.markdown.value,
        status=SourceStatus.pending.value,
        content_sha256="",
        content_bytes=0,
    )
    result = _service().ingest(source=source, content=bytes_, tenant_id="default", workspace_id="default")
    assert result.source.status == SourceStatus.ingested.value
    assert result.source.n_chunks > 0
    assert all(chunk.source_id == "src-1" for chunk in result.chunks)
    assert any("Scope" in (c.section_path or "") for c in result.chunks)


def test_ingest_unknown_kind_falls_through_to_universal_loader():
    # The default registry installs a plain-text ``TextLoader`` as the
    # fallback, so an unknown kind does not raise -- it degrades to
    # UTF-8 text extraction. We assert the source is ingested rather
    # than rejected.
    source = SourceRow(
        id="src-1",
        tenant_id="default",
        workspace_id="default",
        kind=SourceKind.unknown.value,
        status=SourceStatus.pending.value,
        content_sha256="",
        content_bytes=0,
    )
    result = _service().ingest(
        source=source,
        content=b"hello world\n\nsecond paragraph",
        tenant_id="default",
        workspace_id="default",
    )
    assert result.source.status == SourceStatus.ingested.value
    assert result.source.n_chunks > 0


def test_ingest_raises_empty_source_on_no_content():
    source = SourceRow(
        id="src-1",
        tenant_id="default",
        workspace_id="default",
        kind=SourceKind.text.value,
        status=SourceStatus.pending.value,
        content_sha256="",
        content_bytes=0,
    )
    with pytest.raises(EmptySource):
        _service().ingest(source=source, content=b"", tenant_id="default", workspace_id="default")
