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

"""Scope threading coverage for :meth:`RetrievalService.search`.

The retrieval surface is the most scope-sensitive read path --
every BM25 + ANN lookup MUST be scoped to ``(tenant_id,
workspace_id)`` so a caller can never read across workspaces.

Two layers:

1. **Fail-closed contract.** Calling ``search()`` without
   ``tenant_id`` / ``workspace_id`` raises
   :class:`MissingTenantContext`. There is no fallback to
   ``'default'`` on the read path -- forgetting to thread the
   scope is a 400, not a silent leak.

2. **Threading via scope-bound proxies.** The corpus +
   vector_store are wrapped with :class:`_ScopedCorpus` /
   :class:`_ScopedVectorStore`, which :class:`HybridRetriever`
   consumes as-is. The proxies bake the scope into every BM25 /
   vector / hydration call. We mock the two corpora, run
   ``search()``, and assert the underlying methods received
   ``tenant_id`` + ``workspace_id`` kwargs.

The :class:`IndexService` (write path) uses a softer default --
covered by ``test_index_service_threads_scope.py``.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flycanon.core.services.retrieval.retrieval_service import (
    RetrievalService,
    _ScopedCorpus,
    _ScopedVectorStore,
)
from flycanon.web.conventions import MissingTenantContext

# ----------------------------------------------------------------------
# Signature contract -- the read path must accept scope kwargs
# ----------------------------------------------------------------------


class TestSearchSignature:
    def test_search_accepts_tenant_and_workspace_kwargs(self):
        sig = inspect.signature(RetrievalService.search)
        params = sig.parameters
        assert "tenant_id" in params, "RetrievalService.search must accept tenant_id"
        assert "workspace_id" in params, "RetrievalService.search must accept workspace_id"
        assert params["tenant_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["workspace_id"].kind == inspect.Parameter.KEYWORD_ONLY


# ----------------------------------------------------------------------
# Scope-bound proxy behaviour -- the core threading layer
# ----------------------------------------------------------------------


class _FakeScopeAwareCorpus:
    """Stand-in for :class:`PostgresCorpus` exposing scope kwargs."""

    def __init__(self) -> None:
        self.bm25_calls: list[dict[str, Any]] = []
        self.get_chunks_calls: list[dict[str, Any]] = []

    async def bm25_search(
        self,
        query: str,
        *,
        top_k: int = 30,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> list[Any]:
        self.bm25_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )
        return []

    async def get_chunks(
        self,
        chunk_ids: list[str],
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> list[Any]:
        self.get_chunks_calls.append(
            {
                "chunk_ids": chunk_ids,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )
        return []


class _FakeScopeAwareVectorStore:
    """Stand-in for the scoped vector store: scope kwargs are required."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> list[Any]:
        self.calls.append(
            {
                "top_k": top_k,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )
        return []


class TestScopedCorpusProxy:
    @pytest.mark.asyncio
    async def test_threads_scope_to_corpus(self):
        inner = _FakeScopeAwareCorpus()
        scoped = _ScopedCorpus(inner, tenant_id="acme", workspace_id="ws-a")
        await scoped.bm25_search("hello", top_k=7)
        await scoped.get_chunks(["c-1", "c-2"])
        assert inner.bm25_calls == [
            {"query": "hello", "top_k": 7, "tenant_id": "acme", "workspace_id": "ws-a"}
        ]
        assert inner.get_chunks_calls == [
            {"chunk_ids": ["c-1", "c-2"], "tenant_id": "acme", "workspace_id": "ws-a"}
        ]


class TestScopedVectorStoreProxy:
    @pytest.mark.asyncio
    async def test_threads_scope_to_store(self):
        inner = _FakeScopeAwareVectorStore()
        scoped = _ScopedVectorStore(inner, tenant_id="acme", workspace_id="ws-a")
        await scoped.search([0.1, 0.2, 0.3], top_k=10)
        assert inner.calls == [{"top_k": 10, "tenant_id": "acme", "workspace_id": "ws-a"}]


# ----------------------------------------------------------------------
# End-to-end search() threading
# ----------------------------------------------------------------------


class _FakeEmbedResult:
    def __init__(self, embeddings: Sequence[Sequence[float]]) -> None:
        self.embeddings = list(embeddings)


def _make_retrieval_service(*, corpus, vector_store) -> RetrievalService:
    """Build a RetrievalService over mock corpora + repositories.

    Only the surface ``search`` exercises is wired; the rest are
    plain ``AsyncMock`` instances so the call doesn't try to talk
    to a real database.
    """
    from flycanon.core.services.retrieval.corpus_factory import CorpusContext

    context = CorpusContext(corpus=corpus, vector_store=vector_store, backend="test")
    embeddings = MagicMock()
    embeddings.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    embeddings.embed_one = AsyncMock(return_value=[0.1, 0.2, 0.3])
    embeddings.model = "fake"
    source_repo = MagicMock()
    source_repo.get_many = AsyncMock(return_value=[])
    chunk_repo = MagicMock()
    chunk_repo.get_many = AsyncMock(return_value=[])
    knowledge_repo = MagicMock()
    knowledge_repo.lookup_published_citations_for_chunks = AsyncMock(return_value={})
    return RetrievalService(
        context=context,
        embeddings=embeddings,
        source_repository=source_repo,
        chunk_repository=chunk_repo,
        knowledge_repository=knowledge_repo,
        default_top_k=5,
        default_per_query_k=20,
        rrf_k=60,
    )


class TestSearchThreading:
    @pytest.mark.asyncio
    async def test_threads_scope_to_both_corpora(self):
        corpus = _FakeScopeAwareCorpus()
        vector_store = _FakeScopeAwareVectorStore()
        svc = _make_retrieval_service(corpus=corpus, vector_store=vector_store)

        result = await svc.search(query="hello", tenant_id="acme", workspace_id="ws-a", top_k=5)
        # Both corpora must have seen the scope kwargs.
        assert len(corpus.bm25_calls) == 1
        assert corpus.bm25_calls[0]["tenant_id"] == "acme"
        assert corpus.bm25_calls[0]["workspace_id"] == "ws-a"
        assert len(vector_store.calls) == 1
        assert vector_store.calls[0]["tenant_id"] == "acme"
        assert vector_store.calls[0]["workspace_id"] == "ws-a"
        # Empty-corpus path returns an empty result list.
        assert result.hits == []

    @pytest.mark.asyncio
    async def test_fails_closed_when_tenant_missing(self):
        corpus = _FakeScopeAwareCorpus()
        vector_store = _FakeScopeAwareVectorStore()
        svc = _make_retrieval_service(corpus=corpus, vector_store=vector_store)

        # Read path is security-critical: missing scope MUST raise,
        # not silently fall back to 'default'.
        with pytest.raises(MissingTenantContext):
            await svc.search(query="hello", top_k=5)
        # Neither corpus should have been touched.
        assert corpus.bm25_calls == []
        assert vector_store.calls == []

    @pytest.mark.asyncio
    async def test_fails_closed_when_workspace_missing(self):
        svc = _make_retrieval_service(
            corpus=_FakeScopeAwareCorpus(),
            vector_store=_FakeScopeAwareVectorStore(),
        )
        with pytest.raises(MissingTenantContext):
            await svc.search(query="hello", tenant_id="acme", top_k=5)

    @pytest.mark.asyncio
    async def test_fails_closed_when_scope_empty_strings(self):
        # Empty strings are not a real scope -- treat as missing.
        svc = _make_retrieval_service(
            corpus=_FakeScopeAwareCorpus(),
            vector_store=_FakeScopeAwareVectorStore(),
        )
        with pytest.raises(MissingTenantContext):
            await svc.search(query="hello", tenant_id="", workspace_id="", top_k=5)
