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

"""Unit tests for the RLM corpus builder + store.

The ObjectStore is an in-memory dict; the source repository is a stub returning
canned rows. No network, no real PDF (the loader/text path covers extraction;
the PDF branch is exercised by monkeypatching ``extract_pdf_pages``).
"""

from __future__ import annotations

import pytest

from flycanon.core.services.query.rlm import corpus as corpus_mod
from flycanon.core.services.query.rlm.corpus import (
    CanonCorpusBuilder,
    Filters,
    SourceMeta,
)
from flycanon.core.services.query.rlm.session import DocCorpus
from flycanon.models.entities.source import SourceRow


class FakeObjectStore:
    """In-memory ObjectStore: ``put`` seeds, ``get`` reads, no I/O."""

    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = blobs

    async def put(self, key, data, content_type=None):
        self._blobs[key] = data

    async def get(self, key: str) -> bytes:
        return self._blobs[key]

    async def delete(self, key):
        self._blobs.pop(key, None)

    async def exists(self, key) -> bool:
        return key in self._blobs


class FakeSourceRepository:
    """Returns canned rows; honours the ``statuses`` query filter + paging."""

    def __init__(self, rows: list[SourceRow]):
        self._rows = rows

    async def list_sources(
        self,
        *,
        statuses=None,
        kinds=None,
        limit=50,
        offset=0,
        tenant_id=None,
        workspace_id=None,
    ):
        rows = [
            r
            for r in self._rows
            if (tenant_id is None or r.tenant_id == tenant_id)
            and (workspace_id is None or r.workspace_id == workspace_id)
            and (not statuses or r.status in set(statuses))
        ]
        total = len(rows)
        return rows[offset : offset + limit], total


def _row(
    source_id,
    *,
    kind="text",
    filename="doc.txt",
    object_store_key="k",
    status="ready",
    metadata_json=None,
):
    return SourceRow(
        id=source_id,
        tenant_id="t1",
        workspace_id="w1",
        kind=kind,
        status=status,
        filename=filename,
        object_store_key=object_store_key,
        content_sha256="sha",
        metadata_json=metadata_json or {},
    )


@pytest.mark.asyncio
async def test_text_source_loads_as_pages_and_keys_are_readable():
    rows = [_row("s1", filename="Cleargate Business Idea.txt", object_store_key="k1")]
    blobs = {"k1": b"the scope is broad"}
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore(blobs),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")

    assert store.keys() == ["Cleargate Business Idea"]  # filename stem, no extension
    key = store.keys()[0]
    assert store[key] == "the scope is broad"
    assert store.pages(key) == ["the scope is broad"]
    assert store.npages(key) == 1
    assert key in store


@pytest.mark.asyncio
async def test_satisfies_doccorpus_protocol():
    rows = [_row("s1", object_store_key="k1")]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"hello"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert isinstance(store, DocCorpus)


@pytest.mark.asyncio
async def test_source_without_object_store_key_is_skipped():
    rows = [
        _row("s1", filename="kept.txt", object_store_key="k1"),
        _row("s2", filename="missing.txt", object_store_key=None),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"body"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert store.keys() == ["kept"]


@pytest.mark.asyncio
async def test_source_ids_filter_restricts_keys():
    rows = [
        _row("s1", filename="one.txt", object_store_key="k1"),
        _row("s2", filename="two.txt", object_store_key="k2"),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(source_ids=["s2"]),
    )
    assert store.keys() == ["two"]
    assert store.resolve("two").source_id == "s2"


@pytest.mark.asyncio
async def test_metadata_filters_compose_with_and():
    rows = [
        _row("s1", filename="a.txt", object_store_key="k1", metadata_json={"domain": "process"}),
        _row("s2", filename="b.txt", object_store_key="k2", metadata_json={"domain": "compliance"}),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(domains=["compliance"]),
    )
    assert store.keys() == ["b"]


@pytest.mark.asyncio
async def test_tags_filter_matches_on_overlap():
    rows = [
        _row("s1", filename="a.txt", object_store_key="k1", metadata_json={"tags": ["x", "y"]}),
        _row("s2", filename="b.txt", object_store_key="k2", metadata_json={"tags": ["z"]}),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(tags=["y"]),
    )
    assert store.keys() == ["a"]


@pytest.mark.asyncio
async def test_statuses_filter_pushed_to_repository():
    rows = [
        _row("s1", filename="pub.txt", object_store_key="k1", status="published"),
        _row("s2", filename="draft.txt", object_store_key="k2", status="draft"),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(statuses=["published"]),
    )
    assert store.keys() == ["pub"]


@pytest.mark.asyncio
async def test_keys_are_deduplicated():
    rows = [
        _row("s1", filename="report.txt", object_store_key="k1"),
        _row("s2", filename="report.txt", object_store_key="k2"),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert store.keys() == ["report", "report#2"]


@pytest.mark.asyncio
async def test_accessed_tracks_touched_keys_only():
    rows = [
        _row("s1", filename="a.txt", object_store_key="k1"),
        _row("s2", filename="b.txt", object_store_key="k2"),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert store.accessed == []
    _ = store["a"]
    _ = store.pages("a")  # same key again -- still deduped
    assert store.accessed == ["a"]
    _ = store.pages("b")
    assert store.accessed == ["a", "b"]


@pytest.mark.asyncio
async def test_resolve_returns_source_pointer():
    rows = [
        _row(
            "s1",
            filename="x.txt",
            object_store_key="k1",
            metadata_json={"title": "My Title"},
        )
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    meta = store.resolve("x")
    assert isinstance(meta, SourceMeta)
    assert meta.source_id == "s1"
    assert meta.filename == "x.txt"
    assert meta.title == "My Title"
    assert meta.kind == "text"
    assert store.resolve("nope") is None


@pytest.mark.asyncio
async def test_pdf_branch_uses_extract_helper(monkeypatch):
    monkeypatch.setattr(
        corpus_mod,
        "extract_pdf_pages",
        lambda data: ["page one text", "page two text"],
    )
    rows = [_row("s1", kind="pdf", filename="filing.pdf", object_store_key="k1")]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"%PDF-fake"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert store.keys() == ["filing"]
    assert store.npages("filing") == 2
    assert store.pages("filing") == ["page one text", "page two text"]
    assert store["filing"] == "page one text\npage two text"


@pytest.mark.asyncio
async def test_key_fallbacks_to_title_then_id():
    rows = [
        _row("s1", filename=None, object_store_key="k1", metadata_json={"title": "Titled Doc"}),
        _row("s2", filename=None, object_store_key="k2", metadata_json={}),
    ]
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert set(store.keys()) == {"Titled Doc", "s2"}
