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

"""Embedding sets: the binding, the registry table, and the atomic switch.

The set is what turns a change of embedder from a schema fight into an UPDATE,
so the tests here are about the two properties that makes true: the switch is
one column and it is reversible, and nothing reads or writes a vector without
saying which embedding space it belongs to.
"""

from __future__ import annotations

import asyncio

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.embedding_sets import (
    EmbeddingSetBinding,
    EmbeddingSetError,
    EmbeddingSetService,
    bind_embedding_set,
    current_embedding_set,
    split_embedding_model,
)
from flycanon.models.entities.embedding_set import (
    config_fingerprint,
    index_name_for,
    new_embedding_set_id,
)
from flycanon.models.entities.workspace import Workspace
from flycanon.models.repositories.embedding_set_repository import EmbeddingSetRepository

_TENANT = "t-1"
_WORKSPACE = "w-1"


@pytest.fixture
def settings() -> CanonSettings:
    return CanonSettings(embedding_model="ollama:nomic-embed-text", embedding_dimensions=768)


@pytest.fixture
async def service(session_factory, engine, settings) -> EmbeddingSetService:
    repository = EmbeddingSetRepository(session_factory, engine=engine)
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=_WORKSPACE, tenant_id=_TENANT, name="Ops"))
    return EmbeddingSetService(repository=repository, settings=settings)


class TestBinding:
    def test_nothing_is_bound_outside_a_block(self) -> None:
        assert current_embedding_set() is None

    def test_bind_is_scoped_to_the_block(self) -> None:
        binding = EmbeddingSetBinding(set_id="es-1", provider="azure", model="emb", dimensions=3072)
        with bind_embedding_set(binding):
            assert current_embedding_set() is binding
        assert current_embedding_set() is None

    def test_bindings_nest_and_unwind(self) -> None:
        outer = EmbeddingSetBinding(set_id="es-old", provider="ollama", model="n", dimensions=768)
        inner = EmbeddingSetBinding(set_id="es-new", provider="azure", model="e", dimensions=3072)
        with bind_embedding_set(outer):
            with bind_embedding_set(inner):
                assert current_embedding_set() == inner
            assert current_embedding_set() == outer

    async def test_a_binding_does_not_leak_between_concurrent_tasks(self) -> None:
        """The dual-set window depends on this.

        A reindex holds the TARGET set open while the serving path in the same
        process holds the ACTIVE one; if the ContextVar leaked between tasks,
        a query during a re-embed would search the half-built set.
        """
        target = EmbeddingSetBinding(set_id="es-new", provider="azure", model="e", dimensions=3072)
        active = EmbeddingSetBinding(set_id="es-old", provider="ollama", model="n", dimensions=768)
        seen: dict[str, str | None] = {}

        async def _hold(name: str, binding: EmbeddingSetBinding) -> None:
            with bind_embedding_set(binding):
                await asyncio.sleep(0)
                bound = current_embedding_set()
                seen[name] = bound.set_id if bound else None

        await asyncio.gather(_hold("reindex", target), _hold("search", active))
        assert seen == {"reindex": "es-new", "search": "es-old"}

    def test_embedding_model_is_the_provider_prefixed_identifier(self) -> None:
        binding = EmbeddingSetBinding(set_id="es-1", provider="azure", model="dep", dimensions=1536)
        assert binding.embedding_model == "azure:dep"


class TestIdentity:
    def test_set_ids_are_prefixed_and_unique(self) -> None:
        ids = {new_embedding_set_id() for _ in range(100)}
        assert len(ids) == 100
        assert all(i.startswith("es-") for i in ids)

    def test_index_name_is_derived_from_the_set_id(self) -> None:
        name = index_name_for("es-abc-def")
        assert name == "canon_chunk_vectors_hnsw_es_abc_def"

    def test_the_fingerprint_separates_two_azure_resources(self) -> None:
        """The same deployment name on two resources is two different models.

        On Azure ``model=`` is a deployment name an operator chooses, and
        nothing stops two resources pointing the same name at different
        weights -- so the endpoint has to be in the hash.
        """
        one = config_fingerprint(
            provider="azure",
            model="emb",
            dimensions=3072,
            endpoint="https://a.openai.azure.com",
            api_version="2026-05-01",
        )
        two = config_fingerprint(
            provider="azure",
            model="emb",
            dimensions=3072,
            endpoint="https://b.openai.azure.com",
            api_version="2026-05-01",
        )
        assert one != two

    def test_the_fingerprint_is_stable_for_the_same_configuration(self) -> None:
        kwargs = dict(
            provider="openai",
            model="text-embedding-3-large",
            dimensions=3072,
            endpoint="",
            api_version="",
        )
        assert config_fingerprint(**kwargs) == config_fingerprint(**kwargs)  # type: ignore[arg-type]


class TestSplitEmbeddingModel:
    def test_splits_provider_and_model(self) -> None:
        assert split_embedding_model("azure:my-deploy") == ("azure", "my-deploy")

    def test_lowercases_only_the_provider(self) -> None:
        # The deployment name is case-sensitive on Azure; the provider token
        # is ours.
        assert split_embedding_model("AZURE:My-Deploy") == ("azure", "My-Deploy")

    @pytest.mark.parametrize("value", ["nomic-embed-text", "azure:", ":model", ""])
    def test_refuses_anything_that_is_not_provider_colon_model(self, value: str) -> None:
        with pytest.raises(EmbeddingSetError, match="provider.*model"):
            split_embedding_model(value)


class TestService:
    async def test_a_workspace_starts_with_no_set(self, service: EmbeddingSetService) -> None:
        assert await service.active_binding(tenant_id=_TENANT, workspace_id=_WORKSPACE) is None

    async def test_ensure_active_mints_from_the_process_default(self, service: EmbeddingSetService) -> None:
        binding = await service.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert binding.embedding_model == "ollama:nomic-embed-text"
        assert binding.dimensions == 768
        # ...and the workspace now points at it, so the next call is a read.
        again = await service.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert again.set_id == binding.set_id
        assert len(await service.list_for(tenant_id=_TENANT, workspace_id=_WORKSPACE)) == 1

    async def test_activate_switches_and_retires_the_previous(self, service: EmbeddingSetService) -> None:
        first = await service.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        second = await service.create(
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            provider="azure",
            model="text-embedding-3-large",
            dimensions=3072,
            status="ready",
        )
        retired = await service.activate(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=second.id)
        assert retired == first.set_id
        active = await service.active_binding(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert active is not None and active.set_id == second.id
        rows = {r.id: r for r in await service.list_for(tenant_id=_TENANT, workspace_id=_WORKSPACE)}
        assert rows[first.set_id].status == "retired"
        assert rows[first.set_id].retired_at is not None
        assert rows[second.id].status == "active"
        assert rows[second.id].activated_at is not None

    async def test_rollback_restores_the_previous_set(self, service: EmbeddingSetService) -> None:
        first = await service.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        second = await service.create(
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            provider="azure",
            model="emb",
            dimensions=3072,
            status="ready",
        )
        await service.activate(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=second.id)
        restored = await service.rollback(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert restored == first.set_id
        active = await service.active_binding(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert active is not None and active.set_id == first.set_id

    async def test_rollback_without_a_retired_set_says_so(self, service: EmbeddingSetService) -> None:
        await service.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        with pytest.raises(EmbeddingSetError, match="no retired embedding set"):
            await service.rollback(tenant_id=_TENANT, workspace_id=_WORKSPACE)

    async def test_a_building_set_cannot_be_activated(self, service: EmbeddingSetService) -> None:
        """A set that is still building holds an incomplete corpus.

        Activating it would silently narrow what the workspace can find, which
        is the class of failure embedding sets exist to eliminate.
        """
        building = await service.create(
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            provider="azure",
            model="emb",
            dimensions=3072,
            status="building",
        )
        with pytest.raises(EmbeddingSetError, match="building"):
            await service.activate(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=building.id)

    async def test_activating_an_unknown_set_is_refused(self, service: EmbeddingSetService) -> None:
        with pytest.raises(EmbeddingSetError, match="does not exist"):
            await service.activate(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id="es-nope")

    async def test_a_dangling_pointer_names_the_repair(
        self, service: EmbeddingSetService, session_factory
    ) -> None:
        binding = await service.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        await service.delete(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=binding.set_id)
        with pytest.raises(EmbeddingSetError, match="flycanon reindex --activate"):
            await service.active_binding(tenant_id=_TENANT, workspace_id=_WORKSPACE)

    async def test_two_sets_on_the_same_configuration_are_allowed(self, service: EmbeddingSetService) -> None:
        """Re-embedding onto the IDENTICAL configuration has to be possible.

        It is what a deployment does after a batch was written badly -- a
        provider outage that produced zero vectors, a model re-hosted under
        the same id. Routing on (model, dimensions) alone could not express
        it, which is why the set has an id and the fingerprint is not unique.
        """
        first = await service.create(
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            provider="ollama",
            model="nomic-embed-text",
            dimensions=768,
        )
        second = await service.create(
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            provider="ollama",
            model="nomic-embed-text",
            dimensions=768,
        )
        assert first.id != second.id
        assert first.config_fingerprint == second.config_fingerprint

    async def test_a_set_carries_the_index_that_will_serve_it(self, service: EmbeddingSetService) -> None:
        row = await service.create(
            tenant_id=_TENANT,
            workspace_id=_WORKSPACE,
            provider="azure",
            model="emb",
            dimensions=1536,
        )
        assert row.index_name == index_name_for(row.id)
        assert row.embedding_model == "azure:emb"
