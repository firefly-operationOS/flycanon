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

"""A query is embedded by the model that produced the corpus it searches.

Before 26.8.0 the query stage used ``FLYCANON_EMBEDDING_MODEL`` whatever the
corpus had been embedded with, which is what made a same-width change of model
silently degrade recall. These tests pin the three consequences of the fix: the
right embedder is chosen, the set is bound for the ANN scan, and a workspace
that is not on this process's embedder produces a warning that names the
command rather than nothing at all.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetBinding, current_embedding_set
from flycanon.core.services.retrieval.corpus_factory import CorpusContext
from flycanon.core.services.retrieval.retrieval_service import RetrievalService, _ScopedVectorStore

_OLLAMA = EmbeddingSetBinding(set_id="es-old", provider="ollama", model="nomic-embed-text", dimensions=768)
_AZURE = EmbeddingSetBinding(
    set_id="es-new", provider="azure", model="text-embedding-3-large", dimensions=3072
)


class _Corpus:
    async def bm25_search(
        self, query: str, *, top_k: int = 30, tenant_id: str = "", workspace_id: str = ""
    ) -> list[Any]:
        return []

    async def get_chunks(
        self, chunk_ids: list[str], *, tenant_id: str = "", workspace_id: str = ""
    ) -> list[Any]:
        return []


class _VectorStore:
    """Records the set that was bound when the ANN scan ran."""

    def __init__(self) -> None:
        self.searches: list[str | None] = []

    async def search(
        self, query_embedding: list[float], top_k: int = 5, *, tenant_id: str, workspace_id: str
    ) -> list[Any]:
        bound = current_embedding_set()
        self.searches.append(bound.set_id if bound else None)
        return []


def _embedder(model: str, dimensions: int) -> MagicMock:
    service = MagicMock()
    service.model = model
    service.dimensions = dimensions
    service.embed = AsyncMock(return_value=[[0.5] * dimensions])
    service.embed_one = AsyncMock(return_value=[0.5] * dimensions)
    return service


class _Sets:
    def __init__(self, binding: EmbeddingSetBinding | None) -> None:
        self._binding = binding

    async def active_binding(self, *, tenant_id: str, workspace_id: str):
        return self._binding


class _Registry:
    def __init__(self, default: MagicMock) -> None:
        self.default = default
        self.requested: list[EmbeddingSetBinding] = []
        self._by_set = {
            _OLLAMA.set_id: _embedder("ollama:nomic-embed-text", 768),
            _AZURE.set_id: _embedder("azure:text-embedding-3-large", 3072),
        }

    def for_binding(self, binding: EmbeddingSetBinding) -> MagicMock:
        self.requested.append(binding)
        return self._by_set[binding.set_id]


def _service(
    *, binding: EmbeddingSetBinding | None, store: _VectorStore, sets_wired: bool = True
) -> tuple[RetrievalService, _Registry]:
    default = _embedder("ollama:nomic-embed-text", 768)
    registry = _Registry(default)
    repositories = MagicMock()
    repositories.get_many = AsyncMock(return_value=[])
    knowledge = MagicMock()
    knowledge.lookup_published_citations_for_chunks = AsyncMock(return_value={})
    service = RetrievalService(
        context=CorpusContext(corpus=_Corpus(), vector_store=store, backend="test"),
        embeddings=default,
        source_repository=repositories,
        chunk_repository=repositories,
        knowledge_repository=knowledge,
        default_top_k=5,
        default_per_query_k=20,
        rrf_k=60,
        embedding_sets=_Sets(binding) if sets_wired else None,
        embedding_registry=registry if sets_wired else None,
    )
    return service, registry


class TestSetAwareQueryEmbedding:
    async def test_the_query_goes_through_the_set_s_own_embedder(self) -> None:
        store = _VectorStore()
        service, registry = _service(binding=_AZURE, store=store)
        await service.search(query="hello", tenant_id="t-1", workspace_id="w-1")
        assert registry.requested == [_AZURE]
        assert store.searches == ["es-new"], "the ANN scan must run inside the bound set"

    async def test_two_workspaces_on_two_sets_get_two_embedders(self) -> None:
        """One shared flycanon, one tenant on Ollama and one on Azure."""
        store = _VectorStore()
        azure_service, azure_registry = _service(binding=_AZURE, store=store)
        ollama_service, ollama_registry = _service(binding=_OLLAMA, store=store)
        await azure_service.search(query="q", tenant_id="t-1", workspace_id="w-azure")
        await ollama_service.search(query="q", tenant_id="t-2", workspace_id="w-ollama")
        assert azure_registry.requested == [_AZURE]
        assert ollama_registry.requested == [_OLLAMA]
        assert store.searches == ["es-new", "es-old"]

    async def test_the_binding_does_not_outlive_the_search(self) -> None:
        store = _VectorStore()
        service, _ = _service(binding=_AZURE, store=store)
        await service.search(query="hello", tenant_id="t-1", workspace_id="w-1")
        assert current_embedding_set() is None

    async def test_a_workspace_with_no_set_keeps_bm25_and_skips_the_dense_half(self) -> None:
        """There are no vectors to find, so asking one embedding space with a
        vector from another is worse than answering on BM25 alone."""
        store = _VectorStore()
        service, registry = _service(binding=None, store=store)
        result = await service.search(query="hello", tenant_id="t-1", workspace_id="w-new")
        assert store.searches == []
        assert registry.requested == []
        assert result.hits == []

    async def test_without_sets_wired_the_pre_26_8_0_shape_is_unchanged(self) -> None:
        """Unit tests and any caller with a mock dense store keep one embedder
        and no binding."""
        store = _VectorStore()
        service, _ = _service(binding=None, store=store, sets_wired=False)
        await service.search(query="hello", tenant_id="t-1", workspace_id="w-1")
        assert store.searches == [None]


class TestDriftWarning:
    async def test_a_workspace_off_the_process_default_warns_and_names_the_command(self, caplog) -> None:
        """What replaced the boot-time width refusal.

        A WARNING and not a refusal, because one process legitimately serves
        workspaces on several sets -- during a re-embed, and permanently in a
        deployment whose tenants chose different embedders. What it must not
        do is stay silent.
        """
        store = _VectorStore()
        service, _ = _service(binding=_AZURE, store=store)
        with caplog.at_level("WARNING"):
            await service.search(query="hello", tenant_id="t-1", workspace_id="w-1")
        assert "is on embedding set es-new" in caplog.text
        assert "this process is configured for ollama:nomic-embed-text @768" in caplog.text
        assert "flycanon reindex --workspace w-1 --to ollama:nomic-embed-text" in caplog.text

    async def test_it_is_logged_once_per_workspace_and_set(self, caplog) -> None:
        store = _VectorStore()
        service, _ = _service(binding=_AZURE, store=store)
        with caplog.at_level("WARNING"):
            for _ in range(3):
                await service.search(query="hello", tenant_id="t-1", workspace_id="w-1")
        assert caplog.text.count("is on embedding set es-new") == 1

    async def test_a_workspace_on_the_process_default_is_silent(self, caplog) -> None:
        store = _VectorStore()
        service, _ = _service(binding=_OLLAMA, store=store)
        with caplog.at_level("WARNING"):
            await service.search(query="hello", tenant_id="t-1", workspace_id="w-1")
        assert "is on embedding set" not in caplog.text


class TestScopedVectorStoreSwitch:
    @pytest.mark.parametrize("enabled,expected", [(True, 1), (False, 0)])
    async def test_the_dense_half_can_be_switched_off(self, enabled: bool, expected: int) -> None:
        inner = _VectorStore()
        scoped = _ScopedVectorStore(inner, tenant_id="t", workspace_id="w", enabled=enabled)
        assert await scoped.search([0.1], top_k=3) == []
        assert len(inner.searches) == expected
