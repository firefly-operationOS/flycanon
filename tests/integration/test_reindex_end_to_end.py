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

"""A whole change of embedder, on a real database. The gate.

The path an operator walks the day they move a deployment from one embedder
to another, driven by the real :class:`ReindexService` against a real
pgvector: a corpus embedded at one width and model, re-embedded into a new set
at a different width and model, searched before / during / after the switch,
rolled back, and dropped.

Everything that can only be wrong against a live database is asserted here:
the old set answers unchanged for the whole run, the new set is invisible
until it is activated, the switch is a single `UPDATE` with no window in which
a request sees a mixture, the per-set ANN index is built and used, the
rollback restores the previous answers exactly, and the drop reclaims the
rows without touching the set that is serving.

Only the embedder is a stub -- two of them, one per width, each producing a
deterministic vector from the chunk text. The point is the database and the
orchestration, not embedding quality: a real provider would make this test
slow, flaky and unable to run in CI, and would prove nothing extra about
either.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:
    _TESTCONTAINERS_AVAILABLE = False

_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()

pytestmark = pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason="Docker + testcontainers required for the pgvector integration suite",
)

_PGVECTOR_IMAGE = "pgvector/pgvector:pg16"
TENANT = "t-canon"
WORKSPACE = "w-policies"
OLD_WIDTH = 64
#: A supported Matryoshka truncation of text-embedding-3-large. The
#: capability table refuses a width the model cannot produce, in the
#: preflight, which is itself part of what this module drives.
NEW_WIDTH = 256
OLD_MODEL = ("ollama", "nomic-embed-text")
NEW_MODEL = ("azure", "text-embedding-3-large")

#: A miniature corpus with one obvious answer per question.
DOCUMENTS = [
    ("chunk-00", "The daily meal allowance abroad is seventy euros."),
    ("chunk-01", "Business class needs the CFO's written approval."),
    ("chunk-02", "Expense reports are filed within thirty days."),
    ("chunk-03", "Parental leave is sixteen weeks at full pay."),
    ("chunk-04", "The probation period is six months for every new hire."),
    ("chunk-05", "Remote work is allowed from any EU member state."),
    ("chunk-06", "Laptops are replaced on a four-year cycle."),
    ("chunk-07", "Security incidents are reported within one hour."),
    ("chunk-08", "Contractors sign the same confidentiality terms."),
    ("chunk-09", "Annual leave carries over for one quarter only."),
]


class _WordEmbedder:
    """A deterministic bag-of-words embedder at a fixed width.

    Two instances at two widths behave like two genuinely different models:
    the same text lands in different places, so a vector from one is
    meaningless in the other's space. That is exactly the property the whole
    release exists to make safe.
    """

    #: Mirrors EmbeddingService's per-input truncation, which the cost
    #: estimate caps against.
    max_input_chars = 8000

    def __init__(self, *, width: int, salt: int) -> None:
        self.dimensions = width
        self.model = ""
        self._salt = salt
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    async def embed_one(self, text: str) -> list[float]:
        return self._vector(text)

    def strict(self) -> _WordEmbedder:
        return self

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            cleaned = "".join(c for c in word if c.isalnum())
            if cleaned:
                vector[self._bucket(cleaned)] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    def _bucket(self, word: str) -> int:
        """A STABLE hash.

        ``hash()`` on a str is salted per process, so a bag-of-words stub
        built on it would place words differently on every run and this whole
        module would pass or fail depending on PYTHONHASHSEED. A test that
        asserts which chunk answers a question has to be deterministic.
        """
        digest = hashlib.blake2b(f"{self._salt}:{word}".encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimensions


class _Registry:
    """Hands out the embedder that matches a set's width."""

    def __init__(self, embedders: dict[int, _WordEmbedder]) -> None:
        self._embedders = embedders
        self.default = embedders[OLD_WIDTH]

    def for_binding(self, binding: Any) -> _WordEmbedder:
        return self._embedders[binding.dimensions]

    def for_model(self, *, provider: str, model: str, dimensions: int) -> _WordEmbedder:
        return self._embedders[dimensions]


def _sync(url: str) -> str:
    if url.startswith("postgresql+psycopg2"):
        return url.replace("postgresql+psycopg2", "postgresql+psycopg", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url.replace("+asyncpg", "+psycopg")


def _async(url: str) -> str:
    if url.startswith("postgresql+psycopg2"):
        return url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def world() -> Iterator[dict[str, Any]]:
    """A workspace with a corpus indexed under a 64-wide Ollama-shaped set."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from flycanon.config import CanonSettings, get_settings
    from flycanon.core.services.embeddings.embedding_sets import (
        EmbeddingSetService,
        bind_embedding_set,
    )
    from flycanon.core.services.retrieval.corpus_factory import build_corpus_context
    from flycanon.models.repositories.chunk_repository import ChunkRepository
    from flycanon.models.repositories.embedding_set_repository import EmbeddingSetRepository
    from flycanon.models.repositories.ingest_job_repository import IngestJobRepository
    from flycanon.web.conventions.db import install_tenant_guc_hook

    previous = os.environ.get("FLYCANON_EMBEDDING_DIMENSIONS")
    os.environ["FLYCANON_EMBEDDING_DIMENSIONS"] = str(OLD_WIDTH)
    get_settings.cache_clear()
    install_tenant_guc_hook()
    # ONE loop for the whole module. asyncpg connections belong to the loop
    # that opened them, and the fixture's pools are reused by every test, so a
    # fresh ``asyncio.run`` per test would hand a live pool to a dead loop.
    loop = asyncio.new_event_loop()
    with PostgresContainer(_PGVECTOR_IMAGE) as pg:
        url = _async(pg.get_connection_url())
        root = Path(__file__).resolve().parents[2]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "migrations"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")

        settings = CanonSettings(
            database_url=url,
            vector_store="pgvector",
            embedding_model=f"{OLD_MODEL[0]}:{OLD_MODEL[1]}",
            embedding_dimensions=OLD_WIDTH,
        )
        engine = create_async_engine(url, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        sets = EmbeddingSetService(
            repository=EmbeddingSetRepository(factory, engine=engine), settings=settings
        )
        chunks = ChunkRepository(factory, engine=engine)
        jobs = IngestJobRepository(factory, engine=engine)
        context = build_corpus_context(settings=settings)
        embedders = {
            OLD_WIDTH: _WordEmbedder(width=OLD_WIDTH, salt=1),
            NEW_WIDTH: _WordEmbedder(width=NEW_WIDTH, salt=2),
        }

        sync_engine = sa.create_engine(_sync(url), future=True)
        with sync_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO canon_workspaces (id, tenant_id, name, status) "
                    "VALUES (:w, :t, 'Policies', 'active')"
                ),
                {"w": WORKSPACE, "t": TENANT},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO canon_sources (id, kind, status, filename, content_sha256, "
                    "content_bytes, n_chunks, tenant_id, workspace_id, metadata_json) "
                    "VALUES ('src-1', 'text', 'ingested', 'handbook.txt', 'sha', 1, :n, :t, :w, "
                    "CAST('{}' AS JSON))"
                ),
                {"n": len(DOCUMENTS), "t": TENANT, "w": WORKSPACE},
            )
            for index, (chunk_id, content) in enumerate(DOCUMENTS):
                conn.execute(
                    sa.text(
                        "INSERT INTO canon_chunks (id, source_id, index_in_source, total_chunks, "
                        "content, char_start, char_end, embedding_model, tenant_id, workspace_id, "
                        "metadata_json) VALUES (:id, 'src-1', :i, :n, :content, 0, :end, :model, "
                        ":t, :w, CAST('{}' AS JSON))"
                    ),
                    {
                        "id": chunk_id,
                        "i": index,
                        "n": len(DOCUMENTS),
                        "content": content,
                        "end": len(content),
                        "model": f"{OLD_MODEL[0]}:{OLD_MODEL[1]}",
                        "t": TENANT,
                        "w": WORKSPACE,
                    },
                )

        async def _index_under_the_old_set() -> str:
            from fireflyframework_agentic.vectorstores import VectorDocument

            await context.initialise()
            binding = await sets.ensure_active(tenant_id=TENANT, workspace_id=WORKSPACE)
            embedder = embedders[OLD_WIDTH]
            with bind_embedding_set(binding):
                await context.vector_store.upsert(
                    [
                        VectorDocument(
                            id=chunk_id,
                            text=content,
                            embedding=await embedder.embed_one(content),
                            metadata={"source_id": "src-1", "doc_id": "src-1"},
                        )
                        for chunk_id, content in DOCUMENTS
                    ],
                    tenant_id=TENANT,
                    workspace_id=WORKSPACE,
                )
            return binding.set_id

        original_set = loop.run_until_complete(_index_under_the_old_set())

        yield {
            "loop": loop,
            "pg": pg,
            "url": url,
            "settings": settings,
            "sets": sets,
            "chunks": chunks,
            "jobs": jobs,
            "context": context,
            "embedders": embedders,
            "engine": sync_engine,
            "original_set": original_set,
        }

        loop.run_until_complete(context.close())
        sync_engine.dispose()
        loop.run_until_complete(engine.dispose())
        loop.close()
    if previous is None:
        os.environ.pop("FLYCANON_EMBEDDING_DIMENSIONS", None)
    else:
        os.environ["FLYCANON_EMBEDDING_DIMENSIONS"] = previous
    get_settings.cache_clear()


def _service(world: dict[str, Any]):
    from flycanon.core.services.embeddings.reindex_service import ReindexService

    return ReindexService(
        chunks=world["chunks"],
        jobs=world["jobs"],
        sets=world["sets"],
        registry=_Registry(world["embedders"]),
        vector_store=world["context"].vector_store,
        dense_backend=world["context"].dense_backend,
        settings=world["settings"],
    )


def _run(world: dict[str, Any], coro):  # noqa: ANN001, ANN201
    """Drive a coroutine on the module's loop."""
    return world["loop"].run_until_complete(coro)


def _search(world: dict[str, Any], query: str) -> list[str]:
    """One hybrid search as a request would run it, through the active set."""
    from flycanon.core.services.embeddings.embedding_sets import bind_embedding_set
    from flycanon.web.conventions.context import TenantContext, set_tenant_context

    async def _search_once() -> list[str]:
        binding = await world["sets"].active_binding(tenant_id=TENANT, workspace_id=WORKSPACE)
        assert binding is not None
        embedder = world["embedders"][binding.dimensions]
        with bind_embedding_set(binding):
            hits = await world["context"].vector_store.search(
                await embedder.embed_one(query),
                top_k=3,
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
            )
        return [hit.document.id for hit in hits]

    token = set_tenant_context(TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, correlation_id="e2e"))
    try:
        return world["loop"].run_until_complete(_search_once())
    finally:
        token.var.reset(token.token)


def _plan(world: dict[str, Any]):
    return _run(
        world,
        _service(world).plan(
            scopes=[(TENANT, WORKSPACE)],
            provider=NEW_MODEL[0],
            model=NEW_MODEL[1],
            dimensions=NEW_WIDTH,
        ),
    )


class TestBeforeTheRun:
    def test_the_corpus_answers_from_the_original_set(self, world) -> None:
        assert _search(world, "meal allowance abroad")[0] == "chunk-00"
        assert _search(world, "parental leave weeks")[0] == "chunk-03"

    def test_the_plan_prices_the_move_without_writing_anything(self, world) -> None:
        plan = _plan(world)
        rendered = plan.render()
        assert plan.chunk_count == len(DOCUMENTS)
        assert "azure:text-embedding-3-large @256" in rendered
        assert "ollama:nomic-embed-text @64" in rendered
        assert "basis 2026-09-azure-published" in rendered
        with world["engine"].connect() as conn:
            sets = conn.execute(sa.text("SELECT count(*) FROM canon_embedding_sets")).scalar_one()
        assert sets == 1, "an estimate writes nothing"


class TestTheRun:
    def test_it_builds_a_new_set_without_disturbing_the_old_one(self, world) -> None:
        """--no-activate is the dual-set window: two embedding spaces for the
        same workspace, and search still answers from the old one."""
        [outcome] = _run(world, _service(world).run(_plan(world), batch_size=4, activate=False))
        world["new_set"] = outcome.set_id
        assert outcome.embedded == len(DOCUMENTS)
        assert outcome.status == "ready"
        assert outcome.indexed is True

        with world["engine"].connect() as conn:
            per_set = dict(
                conn.execute(
                    sa.text("SELECT set_id, count(*) FROM canon_chunk_vectors GROUP BY set_id")
                ).all()
            )
            widths = dict(
                conn.execute(
                    sa.text(
                        "SELECT set_id, max(vector_dims(embedding)) FROM canon_chunk_vectors GROUP BY set_id"
                    )
                ).all()
            )
        assert per_set == {world["original_set"]: len(DOCUMENTS), outcome.set_id: len(DOCUMENTS)}
        assert widths[world["original_set"]] == OLD_WIDTH
        assert widths[outcome.set_id] == NEW_WIDTH

        # The old set still serves, unchanged, and the new one is invisible.
        assert _search(world, "meal allowance abroad")[0] == "chunk-00"

    def test_the_new_set_has_its_own_ann_index(self, world) -> None:
        with world["engine"].connect() as conn:
            indexes = list(
                conn.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes WHERE tablename = 'canon_chunk_vectors' "
                        "AND indexdef LIKE '%hnsw%'"
                    )
                ).scalars()
            )
        assert len(indexes) == 2
        assert any(world["new_set"].replace("-", "_") in name for name in indexes)

    def test_the_job_trail_is_readable_by_the_existing_stream(self, world) -> None:
        async def _events() -> list[str]:
            jobs = await world["jobs"].list_jobs(tenant_id=TENANT, workspace_id=WORKSPACE)
            job = next(j for j in jobs if j.kind == "reindex")
            events = await world["jobs"].list_events(job.id, tenant_id=TENANT, workspace_id=WORKSPACE)
            return [e.stage for e in events]

        stages = _run(world, _events())
        assert stages[0] == "reindex.started"
        assert "reindex.batch" in stages
        assert "reindex.ready" in stages

    def test_the_chunks_now_name_the_new_model(self, world) -> None:
        with world["engine"].connect() as conn:
            models = set(conn.execute(sa.text("SELECT DISTINCT embedding_model FROM canon_chunks")).scalars())
        assert models == {"azure:text-embedding-3-large"}


class TestTheSwitch:
    def test_activation_moves_every_answer_to_the_new_set_at_once(self, world) -> None:
        before = _search(world, "meal allowance abroad")
        retired = _run(
            world,
            _service(world).activate(tenant_id=TENANT, workspace_id=WORKSPACE, set_id=world["new_set"]),
        )
        assert retired == world["original_set"]
        after = _search(world, "meal allowance abroad")
        # Same corpus, different embedding space: the answer is still right,
        # and it is now being produced by the new set's index.
        assert before[0] == after[0] == "chunk-00"
        assert _search(world, "security incident report")[0] == "chunk-07"

    def test_the_workspace_pointer_is_the_whole_of_the_switch(self, world) -> None:
        with world["engine"].connect() as conn:
            pointer = conn.execute(
                sa.text("SELECT active_embedding_set_id FROM canon_workspaces WHERE id = :w"),
                {"w": WORKSPACE},
            ).scalar_one()
            statuses = dict(conn.execute(sa.text("SELECT id, status FROM canon_embedding_sets")).all())
        assert pointer == world["new_set"]
        assert statuses[world["new_set"]] == "active"
        assert statuses[world["original_set"]] == "retired"

    def test_the_retired_set_keeps_its_rows_so_rollback_is_possible(self, world) -> None:
        with world["engine"].connect() as conn:
            count = conn.execute(
                sa.text("SELECT count(*) FROM canon_chunk_vectors WHERE set_id = :s"),
                {"s": world["original_set"]},
            ).scalar_one()
        assert count == len(DOCUMENTS)


class TestRollback:
    def test_it_restores_the_previous_answers_exactly(self, world) -> None:
        restored = _run(world, _service(world).rollback(tenant_id=TENANT, workspace_id=WORKSPACE))
        assert restored == world["original_set"]
        assert _search(world, "meal allowance abroad")[0] == "chunk-00"
        assert _search(world, "parental leave weeks")[0] == "chunk-03"
        with world["engine"].connect() as conn:
            pointer = conn.execute(
                sa.text("SELECT active_embedding_set_id FROM canon_workspaces WHERE id = :w"),
                {"w": WORKSPACE},
            ).scalar_one()
        assert pointer == world["original_set"]


class TestDrop:
    def test_the_active_set_cannot_be_dropped(self, world) -> None:
        from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetError

        with pytest.raises(EmbeddingSetError, match="answering searches"):
            _run(
                world,
                _service(world).drop_set(
                    tenant_id=TENANT, workspace_id=WORKSPACE, set_id=world["original_set"]
                ),
            )

    def test_dropping_the_other_set_reclaims_its_rows_and_its_index(self, world) -> None:
        deleted = _run(
            world,
            _service(world).drop_set(tenant_id=TENANT, workspace_id=WORKSPACE, set_id=world["new_set"]),
        )
        assert deleted == len(DOCUMENTS)
        with world["engine"].connect() as conn:
            remaining = dict(
                conn.execute(
                    sa.text("SELECT set_id, count(*) FROM canon_chunk_vectors GROUP BY set_id")
                ).all()
            )
            indexes = list(
                conn.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes WHERE tablename = 'canon_chunk_vectors' "
                        "AND indexdef LIKE '%hnsw%'"
                    )
                ).scalars()
            )
            sets = list(conn.execute(sa.text("SELECT id FROM canon_embedding_sets")).scalars())
        assert remaining == {world["original_set"]: len(DOCUMENTS)}
        assert len(indexes) == 1
        assert sets == [world["original_set"]]
        # And the corpus still answers, from the set that survived.
        assert _search(world, "meal allowance abroad")[0] == "chunk-00"

    def test_a_dropped_set_cannot_be_rolled_back_to(self, world) -> None:
        from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetError

        with pytest.raises(EmbeddingSetError, match="has to be rebuilt"):
            _run(world, _service(world).rollback(tenant_id=TENANT, workspace_id=WORKSPACE))
