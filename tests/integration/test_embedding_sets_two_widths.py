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

"""Two embedding spaces, one database, one table -- against a live pgvector.

This is the claim 26.8.0 rests on, and it is not a claim that can be made by
a mock. Until migration 0017 the ``embedding`` column was ``vector(N)`` and
pgvector refused any row of another width at INSERT, so one shared flycanon
meant one width for every tenant, full stop. Here workspace A sits on a
64-wide set and workspace B on a 3072-wide one at the same time, each with its
own partial HNSW, and:

* both searches return their own rows and only their own rows;
* both plans use their OWN index (``EXPLAIN`` is read, not assumed -- a set
  predicate that does not match the index expression character for character
  silently degrades to a sequential scan, which is the failure this design
  would otherwise have no way to notice);
* a query that forgets the set predicate RAISES rather than answering wrongly,
  which is what replaced the boot-time width refusal;
* the coherence trigger refuses a vector whose model disagrees with its set's
  -- the silent-corruption hole that had no guard at all before.

3072 is not an arbitrary large number: it is ``text-embedding-3-large``'s
native width, the exact configuration the Azure preproduction is being stood
up on, and the one pgvector will not build a ``vector`` HNSW for.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]  # noqa: F401

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:
    _TESTCONTAINERS_AVAILABLE = False

_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()

pytestmark = pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason="Docker + testcontainers required for the pgvector integration suite",
)

TENANT = "acme"
SMALL_WORKSPACE = "ws-small"
LARGE_WORKSPACE = "ws-large"
SMALL_SET = "es-small-64"
LARGE_SET = "es-large-3072"
SMALL_WIDTH = 64
#: text-embedding-3-large's native width, and above pgvector's 2000-dimension
#: ceiling for a ``vector`` HNSW -- so this set can only be indexed as halfvec.
LARGE_WIDTH = 3072


def _unit(width: int, axis: int) -> list[float]:
    return [1.0 if i == axis else 0.0 for i in range(width)]


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def _binding(set_id: str, width: int, model: str = "stub:unit"):
    from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetBinding

    provider, _, name = model.partition(":")
    return EmbeddingSetBinding(set_id=set_id, provider=provider, model=name, dimensions=width)


def _store(pg_container, width: int):  # type: ignore[no-untyped-def]
    from flycanon.core.services.retrieval.pgvector_store import RlsPgVectorVectorStore

    return RlsPgVectorVectorStore(
        database_url=pg_container.get_connection_url(),
        dimension=width,
        table_name="canon_chunk_vectors",
    )


@pytest.fixture(scope="module", autouse=True)
def two_sets(pg_container, pg_admin_engine: sa.Engine):  # type: ignore[no-untyped-def]
    """Declare two sets at two widths and write one vector into each."""
    from fireflyframework_agentic.vectorstores import TenantScopedVectorStore, VectorDocument

    from flycanon.core.services.embeddings.embedding_sets import bind_embedding_set

    with pg_admin_engine.begin() as conn:
        for set_id, workspace, width in (
            (SMALL_SET, SMALL_WORKSPACE, SMALL_WIDTH),
            (LARGE_SET, LARGE_WORKSPACE, LARGE_WIDTH),
        ):
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_workspaces (id, tenant_id, name, status, active_embedding_set_id)
                    VALUES (:workspace, :tenant, :workspace, 'active', :set_id)
                    ON CONFLICT (id) DO UPDATE
                        SET active_embedding_set_id = EXCLUDED.active_embedding_set_id
                    """
                ),
                {"workspace": workspace, "tenant": TENANT, "set_id": set_id},
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_embedding_sets
                        (id, tenant_id, workspace_id, provider, model, dimensions, status,
                         config_fingerprint)
                    VALUES (:id, :tenant, :workspace, 'stub', 'unit', :width, 'active', 'fp')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": set_id, "tenant": TENANT, "workspace": workspace, "width": width},
            )

    async def _seed() -> None:
        for set_id, workspace, width in (
            (SMALL_SET, SMALL_WORKSPACE, SMALL_WIDTH),
            (LARGE_SET, LARGE_WORKSPACE, LARGE_WIDTH),
        ):
            store = _store(pg_container, width)
            scoped = TenantScopedVectorStore(store)
            try:
                await store.initialise()
                with bind_embedding_set(_binding(set_id, width)):
                    await scoped.upsert(
                        [
                            VectorDocument(
                                id=f"{workspace}-chunk-{index}",
                                text=f"{workspace} document {index}",
                                embedding=_unit(width, index),
                                metadata={"source_id": f"{workspace}-src"},
                            )
                            for index in range(3)
                        ],
                        tenant_id=TENANT,
                        workspace_id=workspace,
                    )
            finally:
                await store.close()

    asyncio.run(_seed())
    yield


class TestCoexistence:
    def test_one_table_holds_both_widths(self, pg_admin_engine: sa.Engine) -> None:
        with pg_admin_engine.connect() as conn:
            widths = dict(
                conn.execute(
                    sa.text(
                        "SELECT set_id, max(vector_dims(embedding)) FROM canon_chunk_vectors "
                        "GROUP BY set_id ORDER BY set_id"
                    )
                ).all()
            )
        assert widths[SMALL_SET] == SMALL_WIDTH
        assert widths[LARGE_SET] == LARGE_WIDTH

    def test_the_same_chunk_id_can_live_in_two_sets(self, pg_admin_engine: sa.Engine) -> None:
        """``PRIMARY KEY (set_id, id)``. This is what makes a re-embed a batch
        job with a rollback instead of a fresh database."""
        with pg_admin_engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_chunk_vectors
                        (id, set_id, namespace, embedding, dim, model, text)
                    VALUES ('shared-chunk', :small, :ns_small, CAST(:v_small AS vector), :w_small,
                            'stub:unit', 'small copy'),
                           ('shared-chunk', :large, :ns_large, CAST(:v_large AS vector), :w_large,
                            'stub:unit', 'large copy')
                    ON CONFLICT (set_id, id) DO NOTHING
                    """
                ),
                {
                    "small": SMALL_SET,
                    "large": LARGE_SET,
                    "ns_small": f"t/{TENANT}/w/{SMALL_WORKSPACE}",
                    "ns_large": f"t/{TENANT}/w/{LARGE_WORKSPACE}",
                    "v_small": _literal(_unit(SMALL_WIDTH, 0)),
                    "v_large": _literal(_unit(LARGE_WIDTH, 0)),
                    "w_small": SMALL_WIDTH,
                    "w_large": LARGE_WIDTH,
                },
            )
            count = conn.execute(
                sa.text("SELECT count(*) FROM canon_chunk_vectors WHERE id = 'shared-chunk'")
            ).scalar_one()
        assert count == 2

    def test_each_set_has_its_own_partial_index_and_the_wide_one_is_halfvec(
        self, pg_admin_engine: sa.Engine
    ) -> None:
        with pg_admin_engine.connect() as conn:
            indexes = dict(
                conn.execute(
                    sa.text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'canon_chunk_vectors' AND indexdef LIKE '%hnsw%'"
                    )
                ).all()
            )
        small = next(d for n, d in indexes.items() if SMALL_SET.replace("-", "_") in n)
        large = next(d for n, d in indexes.items() if LARGE_SET.replace("-", "_") in n)
        assert f"vector({SMALL_WIDTH})" in small and f"set_id = '{SMALL_SET}'" in small
        # 3072 is above pgvector's 2000-dimension ceiling for a vector HNSW;
        # without halfvec this set would have no ANN index at all.
        assert f"halfvec({LARGE_WIDTH})" in large and f"set_id = '{LARGE_SET}'" in large


class TestSearch:
    def test_each_workspace_finds_its_own_rows_and_only_its_own(self, pg_container) -> None:  # type: ignore[no-untyped-def]
        from fireflyframework_agentic.vectorstores import TenantScopedVectorStore

        from flycanon.core.services.embeddings.embedding_sets import bind_embedding_set

        async def _search(set_id: str, workspace: str, width: int) -> list[str]:
            store = _store(pg_container, width)
            scoped = TenantScopedVectorStore(store)
            try:
                await store.initialise()
                with bind_embedding_set(_binding(set_id, width)):
                    hits = await scoped.search(
                        _unit(width, 0), top_k=10, tenant_id=TENANT, workspace_id=workspace
                    )
                return [hit.document.id for hit in hits]
            finally:
                await store.close()

        small = asyncio.run(_search(SMALL_SET, SMALL_WORKSPACE, SMALL_WIDTH))
        large = asyncio.run(_search(LARGE_SET, LARGE_WORKSPACE, LARGE_WIDTH))
        assert all(i.startswith(SMALL_WORKSPACE) or i == "shared-chunk" for i in small), small
        assert all(i.startswith(LARGE_WORKSPACE) or i == "shared-chunk" for i in large), large
        assert f"{SMALL_WORKSPACE}-chunk-0" in small
        assert f"{LARGE_WORKSPACE}-chunk-0" in large
        assert not set(small) & {f"{LARGE_WORKSPACE}-chunk-{i}" for i in range(3)}

    def test_each_plan_uses_its_own_partial_index(self, pg_admin_engine: sa.Engine) -> None:
        """Read the plan rather than trusting the SQL.

        A set predicate that does not match the index expression character for
        character degrades silently to a sequential scan -- correct answers,
        and nothing anywhere says the ANN index stopped being used.
        """
        with pg_admin_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL hnsw.ef_search = 100"))
            # Eight rows is below any cost threshold, so the planner would
            # pick a sequential scan whatever the index says. Turning it off
            # asks the question this test is actually asking: CAN the index
            # serve this ORDER BY, or does the expression not match?
            conn.execute(sa.text("SET LOCAL enable_seqscan = off"))
            # Likewise the sort: with three rows the planner can filter on the
            # btree over set_id and sort them by hand, which proves nothing
            # about the ANN index. Ruling both out leaves exactly one way to
            # answer an ordered nearest-neighbour query -- the per-set HNSW,
            # if and only if its expression matches.
            conn.execute(sa.text("SET LOCAL enable_sort = off"))
            for set_id, width, cast in (
                (SMALL_SET, SMALL_WIDTH, "vector"),
                (LARGE_SET, LARGE_WIDTH, "halfvec"),
            ):
                plan = "\n".join(
                    conn.execute(
                        sa.text(
                            f"EXPLAIN SELECT id FROM canon_chunk_vectors "
                            f"WHERE set_id = '{set_id}' "
                            f"ORDER BY embedding::{cast}({width}) <=> "
                            f"CAST(:probe AS {cast}({width})) LIMIT 3"
                        ),
                        {"probe": _literal(_unit(width, 0))},
                    ).scalars()
                )
                expected = f"canon_chunk_vectors_hnsw_{set_id.replace('-', '_')}"
                assert expected in plan, f"{set_id} fell back to:\n{plan}"

    def test_a_query_that_forgets_the_set_predicate_raises(self, pg_admin_engine: sa.Engine) -> None:
        """This is what replaced the boot-time width refusal.

        The guarantee moves from "the whole deployment is one width" to "a
        mis-scoped query is an error, not a wrong answer" -- strictly stronger,
        and enforced by pgvector rather than by our code.
        """
        with pg_admin_engine.connect() as conn, pytest.raises(Exception) as exc:
            conn.execute(
                sa.text(
                    f"SELECT id FROM canon_chunk_vectors "
                    f"ORDER BY embedding::vector({SMALL_WIDTH}) <=> "
                    f"CAST(:probe AS vector({SMALL_WIDTH})) LIMIT 1"
                ),
                {"probe": _literal(_unit(SMALL_WIDTH, 0))},
            )
        assert f"expected {SMALL_WIDTH} dimensions" in str(exc.value)


class TestCoherenceGuard:
    def test_a_vector_from_another_model_is_refused_by_the_database(self, pg_admin_engine: sa.Engine) -> None:
        """The single most valuable guard in the release.

        Before 26.8.0 a same-width change of model was accepted silently and
        degraded recall with no error and no log line. Now the database
        refuses it, and the message names the command that creates a new
        embedding space properly.
        """
        with pg_admin_engine.begin() as conn, pytest.raises(Exception) as exc:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_chunk_vectors
                        (id, set_id, namespace, embedding, dim, model, text)
                    VALUES ('impostor', :set_id, :ns, CAST(:vec AS vector), :width,
                            'azure:text-embedding-3-small', 'wrong model')
                    """
                ),
                {
                    "set_id": SMALL_SET,
                    "ns": f"t/{TENANT}/w/{SMALL_WORKSPACE}",
                    "vec": _literal(_unit(SMALL_WIDTH, 5)),
                    "width": SMALL_WIDTH,
                },
            )
        message = str(exc.value)
        assert "ONE embedding space" in message
        assert "flycanon reindex" in message

    def test_a_vector_whose_set_does_not_exist_is_refused(self, pg_admin_engine: sa.Engine) -> None:
        with pg_admin_engine.begin() as conn, pytest.raises(Exception) as exc:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_chunk_vectors
                        (id, set_id, namespace, embedding, dim, model, text)
                    VALUES ('orphan', 'es-does-not-exist', :ns, CAST(:vec AS vector), :width,
                            'stub:unit', 'no set')
                    """
                ),
                {
                    "ns": f"t/{TENANT}/w/{SMALL_WORKSPACE}",
                    "vec": _literal(_unit(SMALL_WIDTH, 6)),
                    "width": SMALL_WIDTH,
                },
            )
        assert "names no row in canon_embedding_sets" in str(exc.value)

    def test_the_store_refuses_a_wrong_width_before_the_database_sees_it(self, pg_container) -> None:  # type: ignore[no-untyped-def]
        """A provider that ignores ``dimensions=`` would otherwise land a
        wrong-width row and break the set's index build."""
        from fireflyframework_agentic.exceptions import VectorStoreError
        from fireflyframework_agentic.vectorstores import TenantScopedVectorStore, VectorDocument

        from flycanon.core.services.embeddings.embedding_sets import bind_embedding_set

        async def _write() -> None:
            store = _store(pg_container, SMALL_WIDTH)
            scoped = TenantScopedVectorStore(store)
            try:
                await store.initialise()
                with bind_embedding_set(_binding(SMALL_SET, SMALL_WIDTH)):
                    await scoped.upsert(
                        [
                            VectorDocument(
                                id="wrong-width",
                                text="x",
                                embedding=[0.1] * 128,
                                metadata={},
                            )
                        ],
                        tenant_id=TENANT,
                        workspace_id=SMALL_WORKSPACE,
                    )
            finally:
                await store.close()

        with pytest.raises(VectorStoreError, match="returned 128 dimensions"):
            asyncio.run(_write())


class TestDropSet:
    def test_dropping_a_set_removes_its_rows_and_its_index(
        self, pg_container, pg_admin_engine: sa.Engine
    ) -> None:  # type: ignore[no-untyped-def]
        """And leaves the other set untouched -- the whole point of one table."""

        async def _drop() -> int:
            store = _store(pg_container, LARGE_WIDTH)
            try:
                await store.initialise()
                return await store.drop_set(LARGE_SET, namespace=f"t/{TENANT}/w/{LARGE_WORKSPACE}")
            finally:
                await store.close()

        deleted = asyncio.run(_drop())
        assert deleted >= 3
        with pg_admin_engine.connect() as conn:
            remaining: Any = conn.execute(
                sa.text("SELECT set_id, count(*) FROM canon_chunk_vectors GROUP BY set_id")
            ).all()
            indexes = list(
                conn.execute(
                    sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'canon_chunk_vectors'")
                ).scalars()
            )
        assert dict(remaining).get(LARGE_SET) is None
        assert dict(remaining).get(SMALL_SET, 0) > 0
        assert f"canon_chunk_vectors_hnsw_{LARGE_SET.replace('-', '_')}" not in indexes
        assert f"canon_chunk_vectors_hnsw_{SMALL_SET.replace('-', '_')}" in indexes
