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
    """In-memory ObjectStore: ``put`` seeds, ``get``/``get_sync`` read, no I/O.

    ``get_sync`` records every key it fetches in ``sync_gets`` so tests can
    assert the lazy store fetches exactly the originals the model touches --
    and nothing during ``build()``.
    """

    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = blobs
        self.sync_gets: list[str] = []

    async def put(self, key, data, content_type=None):
        self._blobs[key] = data

    async def get(self, key: str) -> bytes:
        return self._blobs[key]

    def get_sync(self, key: str) -> bytes:
        self.sync_gets.append(key)
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


class FakeKnowledgeRepository:
    """Resolves ``item_id -> {source_id}`` from a canned mapping.

    Mirrors :meth:`KnowledgeRepository.resolve_source_ids_for_items`: the
    union of cited source ids for the given items, scoped to the workspace.
    """

    def __init__(self, item_to_sources: dict[str, set[str]] | None = None):
        self._map = item_to_sources or {}

    async def resolve_source_ids_for_items(self, item_ids, *, tenant_id, workspace_id):
        resolved: set[str] = set()
        for item_id in item_ids:
            resolved |= self._map.get(item_id, set())
        return resolved


def _builder(rows, blobs, *, item_to_sources=None):
    """A CanonCorpusBuilder over fake repositories + an in-memory store."""
    return CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        knowledge_repository=FakeKnowledgeRepository(item_to_sources),
        object_store=FakeObjectStore(blobs),
    )


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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
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
        knowledge_repository=FakeKnowledgeRepository(),
        object_store=FakeObjectStore({"k1": b"a", "k2": b"b"}),
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")
    assert set(store.keys()) == {"Titled Doc", "s2"}


@pytest.mark.asyncio
async def test_knowledge_item_ids_restricts_to_cited_sources():
    rows = [
        _row("s1", filename="one.txt", object_store_key="k1"),
        _row("s2", filename="two.txt", object_store_key="k2"),
        _row("s3", filename="three.txt", object_store_key="k3"),
    ]
    builder = _builder(
        rows,
        {"k1": b"a", "k2": b"b", "k3": b"c"},
        item_to_sources={"ki1": {"s1", "s3"}},
    )
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(knowledge_item_ids=["ki1"]),
    )
    assert set(store.keys()) == {"one", "three"}


@pytest.mark.asyncio
async def test_knowledge_item_ids_citing_nothing_yields_empty_corpus():
    rows = [_row("s1", filename="one.txt", object_store_key="k1")]
    builder = _builder(rows, {"k1": b"a"}, item_to_sources={})
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(knowledge_item_ids=["ki-unknown"]),
    )
    assert store.keys() == []


@pytest.mark.asyncio
async def test_knowledge_item_ids_compose_with_source_ids_and():
    rows = [
        _row("s1", filename="one.txt", object_store_key="k1"),
        _row("s2", filename="two.txt", object_store_key="k2"),
        _row("s3", filename="three.txt", object_store_key="k3"),
    ]
    builder = _builder(
        rows,
        {"k1": b"a", "k2": b"b", "k3": b"c"},
        item_to_sources={"ki1": {"s1", "s2"}},
    )
    # knowledge resolves to {s1, s2}; source_ids restricts to {s2, s3};
    # AND => only s2 survives.
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(knowledge_item_ids=["ki1"], source_ids=["s2", "s3"]),
    )
    assert store.keys() == ["two"]


@pytest.mark.asyncio
async def test_domains_and_tags_both_must_match():
    rows = [
        _row(
            "s1",
            filename="a.txt",
            object_store_key="k1",
            metadata_json={"domain": "compliance", "tags": ["aml"]},
        ),
        _row(
            "s2",
            filename="b.txt",
            object_store_key="k2",
            metadata_json={"domain": "compliance", "tags": ["kyc"]},
        ),
        _row(
            "s3",
            filename="c.txt",
            object_store_key="k3",
            metadata_json={"domain": "process", "tags": ["aml"]},
        ),
    ]
    builder = _builder(rows, {"k1": b"a", "k2": b"b", "k3": b"c"})
    # Only s1 satisfies BOTH domain=compliance AND tag=aml.
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(domains=["compliance"], tags=["aml"]),
    )
    assert store.keys() == ["a"]


@pytest.mark.asyncio
async def test_statuses_and_source_ids_both_must_match():
    rows = [
        _row("s1", filename="a.txt", object_store_key="k1", status="published"),
        _row("s2", filename="b.txt", object_store_key="k2", status="published"),
        _row("s3", filename="c.txt", object_store_key="k3", status="draft"),
    ]
    builder = _builder(rows, {"k1": b"a", "k2": b"b", "k3": b"c"})
    # statuses keeps {s1, s2}; source_ids keeps {s2, s3}; AND => s2.
    store = await builder.build(
        tenant_id="t1",
        workspace_id="w1",
        filters=Filters(statuses=["published"], source_ids=["s2", "s3"]),
    )
    assert store.keys() == ["b"]


@pytest.mark.asyncio
async def test_build_fetches_nothing_and_access_fetches_lazily_once_per_key():
    """build() does zero fetches; first access fetches; re-access is memoised."""
    rows = [
        _row("s1", filename="a.txt", object_store_key="k1"),
        _row("s2", filename="b.txt", object_store_key="k2"),
    ]
    store_blobs = FakeObjectStore({"k1": b"alpha", "k2": b"beta"})
    builder = CanonCorpusBuilder(
        source_repository=FakeSourceRepository(rows),
        knowledge_repository=FakeKnowledgeRepository(),
        object_store=store_blobs,
    )
    store = await builder.build(tenant_id="t1", workspace_id="w1")

    # build() listed both sources but fetched no originals.
    assert store_blobs.sync_gets == []
    assert store.keys() == ["a", "b"]

    # Metadata-only surface never fetches.
    assert "a" in store
    assert len(store) == 2
    assert list(iter(store)) == ["a", "b"]
    assert store.resolve("a").source_id == "s1"
    assert store_blobs.sync_gets == []

    # Accessing two distinct keys triggers exactly two fetches.
    assert store["a"] == "alpha"
    assert store.pages("b") == ["beta"]
    assert store_blobs.sync_gets == ["k1", "k2"]

    # Re-accessing the same keys triggers no additional fetches (memoised).
    assert store["a"] == "alpha"
    assert store.pages("a") == ["alpha"]
    assert store.npages("b") == 1
    assert store["b"] == "beta"
    assert store_blobs.sync_gets == ["k1", "k2"]
