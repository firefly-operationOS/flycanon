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

"""Scope threading coverage for :class:`IndexService`.

The write surface is FAIL-CLOSED: ``tenant_id`` / ``workspace_id`` are REQUIRED
kwargs on both ``replace_for_source`` and ``remove_for_source``. Forgetting the
scope raises ``TypeError`` at the call site instead of corrupting the index.

The dense store is the explicit, scope-aware
:class:`~fireflyframework_agentic.vectorstores.TenantScopedVectorStore`, so the
kwargs are passed straight through to ``upsert`` / ``delete`` (no
signature-probing fallback). ``remove_for_source`` additionally purges the dense
vectors for a deleted source's chunks, since external stores have no FK cascade.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest

from flycanon.core.services.retrieval.corpus_factory import CorpusContext
from flycanon.core.services.retrieval.index_service import IndexService


class _FakeCorpus:
    """Stand-in -- the only thing we care about is the upsert pass-through."""

    def __init__(self) -> None:
        self.delete_calls: list[str] = []
        self.upsert_calls: list[Sequence[Any]] = []

    async def initialise(self) -> None:
        return None

    async def delete_by_doc_id(self, doc_id: str) -> int:
        self.delete_calls.append(doc_id)
        return 0

    async def upsert_chunks(self, chunks: Sequence[Any]) -> None:
        self.upsert_calls.append(chunks)


class _FakeScopeAwareVectorStore:
    """Stand-in for the scoped vector store: scope kwargs are required."""

    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, Any]] = []

    async def upsert(self, documents: list[Any], *, tenant_id: str, workspace_id: str) -> None:
        self.upsert_calls.append(
            {"documents": documents, "tenant_id": tenant_id, "workspace_id": workspace_id}
        )


def _source(id_: str = "src-1"):
    """Stub :class:`SourceRow` -- we only read a handful of attrs."""
    s = MagicMock()
    s.id = id_
    s.kind = "doc"
    s.filename = "f.md"
    s.uri = None
    return s


def _chunk(id_: str, *, index: int = 0):
    """Stub :class:`KnowledgeChunkRow` -- mutable attrs only."""
    c = MagicMock()
    c.id = id_
    c.content = "hello"
    c.section_path = None
    c.page = None
    c.index_in_source = index
    c.embedding_model = None
    return c


def _make_index_service(*, corpus, vector_store) -> IndexService:
    return IndexService(
        context=CorpusContext(corpus=corpus, vector_store=vector_store, backend="test"),
    )


class TestReplaceForSourceSignature:
    def test_requires_explicit_scope(self):
        # Fail-closed: the kwargs MUST be required so any caller that
        # forgets the scope fails loud with a TypeError instead of
        # silently writing into the ('default','default') RLS bucket.
        import inspect

        sig = inspect.signature(IndexService.replace_for_source)
        params = sig.parameters
        assert "tenant_id" in params
        assert "workspace_id" in params
        assert params["tenant_id"].default is inspect.Parameter.empty
        assert params["workspace_id"].default is inspect.Parameter.empty


class TestReplaceForSourceWritePathFailsClosed:
    @pytest.mark.asyncio
    async def test_raises_when_scope_missing(self):
        # Belt-and-braces: forgetting scope kwargs must NOT silently
        # fall back to 'default'. Catching it at the call site is
        # what stops a regression from re-undermining the RLS write
        # path.
        corpus = _FakeCorpus()
        vector_store = _FakeScopeAwareVectorStore()
        svc = _make_index_service(corpus=corpus, vector_store=vector_store)

        chunk = _chunk("ch-1")
        with pytest.raises(TypeError):
            await svc.replace_for_source(  # type: ignore[call-arg]
                source=_source(),
                chunks=[chunk],
                embeddings=[[0.1, 0.2, 0.3]],
                embedding_model="fake",
            )

    @pytest.mark.asyncio
    async def test_passes_explicit_scope_to_vector_store(self):
        corpus = _FakeCorpus()
        vector_store = _FakeScopeAwareVectorStore()
        svc = _make_index_service(corpus=corpus, vector_store=vector_store)

        chunk = _chunk("ch-1")
        await svc.replace_for_source(
            source=_source(),
            chunks=[chunk],
            embeddings=[[0.1, 0.2, 0.3]],
            embedding_model="fake",
            tenant_id="acme",
            workspace_id="ws-a",
        )
        assert vector_store.upsert_calls[0]["tenant_id"] == "acme"
        assert vector_store.upsert_calls[0]["workspace_id"] == "ws-a"


class TestRemoveForSourcePurgesVectors:
    @pytest.mark.asyncio
    async def test_purges_dense_vectors_for_source_chunks(self):
        # remove_for_source must purge the dense vectors explicitly (external
        # stores have no FK cascade). It resolves the source's chunk ids via the
        # chunk repository and deletes them, scoped.
        from unittest.mock import AsyncMock

        corpus = _FakeCorpus()
        vector_store = MagicMock()
        vector_store.delete = AsyncMock()
        chunk_repo = MagicMock()
        chunk_repo.list_for_source = AsyncMock(return_value=[_chunk("ch-1"), _chunk("ch-2")])
        svc = IndexService(
            context=CorpusContext(corpus=corpus, vector_store=vector_store, backend="test"),
            chunk_repository=chunk_repo,
        )

        await svc.remove_for_source("src-1", tenant_id="acme", workspace_id="ws-a")

        chunk_repo.list_for_source.assert_awaited_once_with("src-1")
        vector_store.delete.assert_awaited_once_with(["ch-1", "ch-2"], tenant_id="acme", workspace_id="ws-a")

    @pytest.mark.asyncio
    async def test_requires_explicit_scope(self):
        import inspect

        sig = inspect.signature(IndexService.remove_for_source)
        assert sig.parameters["tenant_id"].default is inspect.Parameter.empty
        assert sig.parameters["workspace_id"].default is inspect.Parameter.empty
