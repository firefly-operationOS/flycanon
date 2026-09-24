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

"""``flycanon reindex``: the plan, the cursor, and the refusals.

The behaviours a re-embed is judged on: it never activates a set it did not
fill, it can be killed and resumed, replaying a batch rewrites rather than
duplicates, a rate limit is a pause and not a failure, and it fails loudly
rather than reporting success over a corpus it could not see.
"""

from __future__ import annotations

from typing import Any

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.embedding_service import EmbeddingThrottled
from flycanon.core.services.embeddings.embedding_sets import (
    EmbeddingSetBinding,
    EmbeddingSetError,
    EmbeddingSetService,
    current_embedding_set,
)
from flycanon.core.services.embeddings.model_capabilities import UnsupportedDimensionsError
from flycanon.core.services.embeddings.reindex_service import ReindexError, ReindexService
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.workspace import Workspace
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.embedding_set_repository import EmbeddingSetRepository
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository

_TENANT = "t-1"
_WORKSPACE = "w-1"
_SCOPE = {"tenant_id": _TENANT, "workspace_id": _WORKSPACE}


class _StubEmbedder:
    """Deterministic vectors, with a programmable throttle."""

    def __init__(self, *, dimensions: int, throttle_after: int | None = None) -> None:
        self.dimensions = dimensions
        self.model = "azure:text-embedding-3-large"
        self.calls: list[list[str]] = []
        self._throttle_after = throttle_after

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._throttle_after is not None and len(self.calls) >= self._throttle_after:
            raise EmbeddingThrottled("provider is rate-limiting", retry_after=2.0)
        self.calls.append(list(texts))
        return [[float(len(t))] * self.dimensions for t in texts]

    def strict(self) -> _StubEmbedder:
        return self


class _StubRegistry:
    def __init__(self, embedder: _StubEmbedder) -> None:
        self._embedder = embedder
        self.default = _Default()

    def for_model(self, **_kwargs: Any) -> _StubEmbedder:
        return self._embedder

    def for_binding(self, _binding: EmbeddingSetBinding) -> _StubEmbedder:
        return self._embedder


class _Default:
    max_input_chars = 8000
    model = "ollama:nomic-embed-text"
    dimensions = 768


class _RecordingVectorStore:
    """Records upserts and the set that was bound when they happened."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], list[float]] = {}
        self.upserts = 0

    async def upsert(self, documents: list[Any], *, tenant_id: str, workspace_id: str) -> None:
        binding = current_embedding_set()
        assert binding is not None, "a reindex write must be bound to its target set"
        self.upserts += 1
        for document in documents:
            # The composite key is what makes a replay a rewrite.
            self.rows[(binding.set_id, document.id)] = list(document.embedding)


class _StubDense:
    def __init__(self, *, can_index: bool = True) -> None:
        self.can_index = can_index
        self.indexed: list[str] = []
        self.dropped: list[str] = []

    async def ensure_set_index(self, binding: EmbeddingSetBinding) -> bool:
        if self.can_index:
            self.indexed.append(binding.set_id)
        return self.can_index

    async def drop_set(self, set_id: str, *, namespace: str) -> int:
        self.dropped.append(set_id)
        return 3


@pytest.fixture
def settings() -> CanonSettings:
    return CanonSettings(embedding_model="ollama:nomic-embed-text", embedding_dimensions=768)


@pytest.fixture
async def world(session_factory, engine, settings):
    """A workspace with nine chunks, on the pre-reindex embedder."""
    chunks = ChunkRepository(session_factory, engine=engine)
    jobs = IngestJobRepository(session_factory, engine=engine)
    sets = EmbeddingSetService(
        repository=EmbeddingSetRepository(session_factory, engine=engine), settings=settings
    )
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=_WORKSPACE, tenant_id=_TENANT, name="Ops"))
        session.add_all(
            [
                KnowledgeChunkRow(
                    id=f"chunk-{i:02d}",
                    source_id="src-1",
                    index_in_source=i,
                    total_chunks=9,
                    content=f"paragraph number {i} " * 4,
                    char_start=0,
                    char_end=10,
                    embedding_model="ollama:nomic-embed-text",
                    metadata_json={},
                    **_SCOPE,
                )
                for i in range(9)
            ]
        )
    embedder = _StubEmbedder(dimensions=3072)
    store = _RecordingVectorStore()
    dense = _StubDense()
    service = ReindexService(
        chunks=chunks,
        jobs=jobs,
        sets=sets,
        registry=_StubRegistry(embedder),
        vector_store=store,
        dense_backend=dense,
        settings=settings,
    )
    return {
        "service": service,
        "sets": sets,
        "chunks": chunks,
        "jobs": jobs,
        "store": store,
        "dense": dense,
        "embedder": embedder,
    }


async def _plan(service: ReindexService, **overrides: Any):
    return await service.plan(
        scopes=[(_TENANT, _WORKSPACE)],
        provider=overrides.get("provider", "azure"),
        model=overrides.get("model", "text-embedding-3-large"),
        dimensions=overrides.get("dimensions", 3072),
    )


class TestScope:
    async def test_exactly_one_selector_is_required(self, world) -> None:
        """No bare ``flycanon reindex`` that silently means ``--all``."""
        service = world["service"]
        with pytest.raises(ReindexError, match="exactly one scope"):
            await service.resolve_scope()
        with pytest.raises(ReindexError, match="exactly one scope"):
            await service.resolve_scope(tenant_id=_TENANT, everything=True)

    async def test_a_workspace_needs_its_tenant(self, world) -> None:
        with pytest.raises(ReindexError, match="--workspace needs --tenant"):
            await world["service"].resolve_scope(workspace_id=_WORKSPACE)

    async def test_all_finds_every_workspace_that_holds_chunks(self, world) -> None:
        assert await world["service"].resolve_scope(everything=True) == [(_TENANT, _WORKSPACE)]

    async def test_a_tenant_selects_its_own_workspaces(self, world) -> None:
        assert await world["service"].resolve_scope(tenant_id=_TENANT) == [(_TENANT, _WORKSPACE)]
        assert await world["service"].resolve_scope(tenant_id="t-other") == []


class TestPlan:
    async def test_it_counts_and_prices_without_writing_anything(self, world) -> None:
        plan = await _plan(world["service"])
        assert plan.chunk_count == 9
        assert plan.estimated_tokens > 0
        assert "azure:text-embedding-3-large @3072" in plan.render()
        assert "basis 2026-09-azure-published" in plan.render()
        assert await world["sets"].list_for(tenant_id=_TENANT, workspace_id=_WORKSPACE) == []

    async def test_the_current_embedder_is_shown_next_to_the_target(self, world) -> None:
        await world["sets"].ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        plan = await _plan(world["service"])
        assert "ollama:nomic-embed-text @768" in plan.render()

    async def test_an_unknown_model_says_so_instead_of_pricing_it_at_zero(self, world) -> None:
        plan = await _plan(world["service"], model="prod-emb", dimensions=1024)
        rendered = plan.render()
        assert "cost unknown" in rendered
        assert "UNKNOWN, not zero" in rendered
        assert "$0.00" not in rendered

    async def test_a_width_the_model_cannot_produce_is_refused_before_the_run(self, world) -> None:
        """In the preflight, not after the first API call of a re-embed."""
        with pytest.raises(UnsupportedDimensionsError):
            await _plan(world["service"], model="text-embedding-ada-002", dimensions=3072)

    async def test_the_estimate_respects_the_input_truncation(self, world, session_factory) -> None:
        """An estimate that ignores ``_MAX_INPUT_CHARS`` overstates the bill.

        On a corpus with a few very long chunks it overstates it by a lot, and
        the number is what an operator uses to decide.
        """
        async with session_factory() as session, session.begin():
            session.add(
                KnowledgeChunkRow(
                    id="chunk-huge",
                    source_id="src-1",
                    index_in_source=99,
                    total_chunks=10,
                    content="x" * 80_000,
                    char_start=0,
                    char_end=1,
                    embedding_model="ollama:nomic-embed-text",
                    metadata_json={},
                    **_SCOPE,
                )
            )
        plan = await _plan(world["service"])
        # 80k chars would be 20k tokens uncapped; capped at 8000 it is 2000.
        assert plan.estimated_tokens < 4000


class TestRun:
    async def test_it_embeds_every_chunk_into_the_new_set_and_activates(self, world) -> None:
        service, store, sets = world["service"], world["store"], world["sets"]
        previous = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        [outcome] = await service.run(await _plan(service), batch_size=4)
        assert outcome.embedded == 9
        assert outcome.status == "ready"
        assert outcome.activated is True
        assert outcome.retired_set_id == previous.set_id
        assert len(store.rows) == 9
        assert all(key[0] == outcome.set_id for key in store.rows)
        active = await sets.active_binding(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert active is not None and active.set_id == outcome.set_id
        assert active.embedding_model == "azure:text-embedding-3-large"

    async def test_the_old_set_still_exists_so_rollback_is_one_update(self, world) -> None:
        service, sets = world["service"], world["sets"]
        previous = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        await service.run(await _plan(service), batch_size=4)
        restored = await service.rollback(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert restored == previous.set_id

    async def test_no_activate_builds_the_set_and_leaves_the_workspace_alone(self, world) -> None:
        service, sets = world["service"], world["sets"]
        previous = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        [outcome] = await service.run(await _plan(service), batch_size=4, activate=False)
        assert outcome.activated is False
        active = await sets.active_binding(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert active is not None and active.set_id == previous.set_id
        rows = {r.id: r for r in await sets.list_for(tenant_id=_TENANT, workspace_id=_WORKSPACE)}
        assert rows[outcome.set_id].status == "ready"
        assert rows[outcome.set_id].vector_count == 9

    async def test_the_chunks_are_restamped_with_the_new_model(self, world) -> None:
        service, chunks = world["service"], world["chunks"]
        await service.run(await _plan(service), batch_size=4)
        rows = await chunks.list_for_workspace(tenant_id=_TENANT, workspace_id=_WORKSPACE, limit=99)
        assert {r.embedding_model for r in rows} == {"azure:text-embedding-3-large"}

    async def test_progress_events_land_on_the_existing_job_stream(self, world) -> None:
        """No new plumbing: the SSE surface an operator already has works."""
        service, jobs = world["service"], world["jobs"]
        await service.run(await _plan(service), batch_size=4)
        [job] = await jobs.list_jobs(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert job.kind == "reindex"
        assert job.status == "succeeded"
        stages = [e.stage for e in await jobs.list_events(job.id, tenant_id=_TENANT, workspace_id=_WORKSPACE)]
        assert stages[0] == "reindex.started"
        assert stages.count("reindex.batch") == 3  # nine chunks, four at a time
        assert "reindex.ready" in stages

    async def test_it_refuses_to_activate_a_set_whose_index_it_could_not_build(self, world) -> None:
        """Activating an unindexed set means serving searches from a
        sequential scan without saying so."""
        world["dense"].can_index = False
        with pytest.raises(ReindexError) as exc:
            await world["service"].run(await _plan(world["service"]), batch_size=4)
        assert "flycanon reindex --activate" in str(exc.value)

    async def test_a_run_that_reads_nothing_from_a_non_empty_workspace_fails(
        self, world, monkeypatch
    ) -> None:
        """The 2026-09-17 bug, as a refusal.

        Under a role whose RLS GUCs are not in force the corpus reads empty; a
        run that "succeeded" would then activate a set with no vectors in it.
        """
        service = world["service"]
        plan = await _plan(service)

        async def _blind(**_kwargs: Any) -> list[Any]:
            return []

        monkeypatch.setattr(world["chunks"], "list_for_workspace", _blind)
        with pytest.raises(ReindexError) as exc:
            await service.run(plan, batch_size=4)
        message = str(exc.value)
        assert "holds 9 chunk(s) but the reindex read 0" in message
        assert "bypasses RLS" in message
        assert "Nothing has been activated" in message

    async def test_an_empty_workspace_is_not_a_failure(self, world, session_factory) -> None:
        async with session_factory() as session, session.begin():
            session.add(Workspace(id="w-empty", tenant_id=_TENANT, name="Empty"))
        plan = await world["service"].plan(
            scopes=[(_TENANT, "w-empty")],
            provider="azure",
            model="text-embedding-3-large",
            dimensions=3072,
        )
        [outcome] = await world["service"].run(plan)
        assert outcome.embedded == 0
        assert outcome.status == "ready"


class TestResumeAndReplay:
    async def test_a_killed_run_resumes_from_its_cursor(self, world) -> None:
        service, jobs, store = world["service"], world["jobs"], world["store"]
        world["embedder"]._throttle_after = 2  # two batches of four, then stop
        [stopped] = await service.run(await _plan(service), batch_size=4)
        assert stopped.status == "throttled"
        assert stopped.embedded == 8
        assert stopped.activated is False
        [job] = await jobs.list_jobs(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        cursor = job.metadata_json["cursor"]
        assert cursor["done"] == 8
        assert cursor["last_chunk_id"] == "chunk-07"

        world["embedder"]._throttle_after = None
        [resumed] = await service.run(await _plan(service), batch_size=4, resume_set_id=stopped.set_id)
        assert resumed.set_id == stopped.set_id, "resume continues the SAME set"
        assert resumed.embedded == 9
        assert resumed.status == "ready"
        # Every chunk embedded exactly once into that set.
        assert len({k[1] for k in store.rows if k[0] == stopped.set_id}) == 9

    async def test_a_throttle_records_the_retry_after_and_does_not_fail_the_job(self, world) -> None:
        """``attempts`` is reserved for genuine errors, so a small TPM quota
        cannot quietly kill an eight-hour run through ingest_max_attempts."""
        service, jobs = world["service"], world["jobs"]
        world["embedder"]._throttle_after = 1
        [outcome] = await service.run(await _plan(service), batch_size=4)
        assert outcome.status == "throttled"
        [job] = await jobs.list_jobs(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert job.attempts == 0
        throttles = [
            e
            for e in await jobs.list_events(job.id, tenant_id=_TENANT, workspace_id=_WORKSPACE)
            if e.stage == "reindex.throttled"
        ]
        assert throttles and throttles[0].payload_json["retry_after_s"] == 2.0

    async def test_replaying_a_run_rewrites_rather_than_duplicating(self, world) -> None:
        """``ON CONFLICT (set_id, id)``. This is why the PK change is not
        cosmetic."""
        service, store = world["service"], world["store"]
        [first] = await service.run(await _plan(service), batch_size=4)
        before = dict(store.rows)
        [second] = await service.run(await _plan(service), batch_size=4, resume_set_id=first.set_id)
        assert second.set_id == first.set_id
        assert store.rows == before
        assert len(store.rows) == 9

    async def test_resuming_an_unknown_set_is_refused(self, world) -> None:
        with pytest.raises(ReindexError, match="does not exist"):
            await world["service"].run(await _plan(world["service"]), resume_set_id="es-nope")


class TestCatchUp:
    async def test_a_chunk_ingested_during_the_run_is_picked_up_before_the_switch(
        self, world, session_factory, monkeypatch
    ) -> None:
        """Otherwise the set that starts answering queries is missing a
        document the operator watched arrive."""
        service, store = world["service"], world["store"]
        chunks = world["chunks"]
        original = chunks.list_for_workspace
        injected = {"done": False}

        async def _listing(**kwargs: Any) -> list[Any]:
            rows = await original(**kwargs)
            if not injected["done"] and kwargs.get("after_id") is None:
                injected["done"] = True
                async with session_factory() as session, session.begin():
                    session.add(
                        KnowledgeChunkRow(
                            id="chunk-zz-late",
                            source_id="src-2",
                            index_in_source=0,
                            total_chunks=1,
                            content="arrived mid-run",
                            char_start=0,
                            char_end=1,
                            embedding_model="ollama:nomic-embed-text",
                            metadata_json={},
                            **_SCOPE,
                        )
                    )
            return rows

        monkeypatch.setattr(chunks, "list_for_workspace", _listing)
        [outcome] = await service.run(await _plan(service), batch_size=4)
        assert (outcome.set_id, "chunk-zz-late") in store.rows
        assert outcome.embedded == 10


class TestDropSet:
    async def test_dropping_the_active_set_is_refused(self, world) -> None:
        service, sets = world["service"], world["sets"]
        active = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        with pytest.raises(EmbeddingSetError, match="answering searches"):
            await service.drop_set(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=active.set_id)

    async def test_dropping_a_retired_set_removes_its_rows_and_its_row(self, world) -> None:
        service, sets, dense = world["service"], world["sets"], world["dense"]
        previous = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        await service.run(await _plan(service), batch_size=4)
        deleted = await service.drop_set(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=previous.set_id)
        assert deleted == 3
        assert dense.dropped == [previous.set_id]
        remaining = await sets.list_for(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        assert previous.set_id not in {r.id for r in remaining}

    async def test_a_dropped_set_cannot_be_rolled_back_to_and_says_so(self, world) -> None:
        service, sets = world["service"], world["sets"]
        previous = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        await service.run(await _plan(service), batch_size=4)
        await service.drop_set(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id=previous.set_id)
        with pytest.raises(EmbeddingSetError, match="has to be rebuilt"):
            await service.rollback(tenant_id=_TENANT, workspace_id=_WORKSPACE)

    async def test_dropping_an_unknown_set_is_refused(self, world) -> None:
        with pytest.raises(ReindexError, match="does not exist"):
            await world["service"].drop_set(tenant_id=_TENANT, workspace_id=_WORKSPACE, set_id="es-nope")


class TestListing:
    async def test_it_marks_the_set_that_answers_searches(self, world) -> None:
        service, sets = world["service"], world["sets"]
        previous = await sets.ensure_active(tenant_id=_TENANT, workspace_id=_WORKSPACE)
        [outcome] = await service.run(await _plan(service), batch_size=4)
        listing = await service.list_sets(scopes=[(_TENANT, _WORKSPACE)])
        active = {entry.row.id for entry in listing if entry.is_active}
        assert active == {outcome.set_id}
        assert previous.set_id in {entry.row.id for entry in listing}
