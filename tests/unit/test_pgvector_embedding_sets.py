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

"""The SQL ``RlsPgVectorVectorStore`` emits once a table holds several widths.

The framework's adapter writes ``ON CONFLICT (id)`` and reads
``embedding <=> $1`` with no cast and no set predicate. On a table that holds
two embedding spaces, the first collides between sets and the second either
raises or uses the wrong index. Both are overridden, and the shape of the
override is what these tests pin -- the behaviour against a live pgvector is
in ``tests/integration/test_embedding_sets_two_widths.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fireflyframework_agentic.exceptions import VectorStoreError
from fireflyframework_agentic.vectorstores.types import VectorDocument

from flycanon.core.services.embeddings.embedding_sets import EmbeddingSetBinding, bind_embedding_set
from flycanon.core.services.retrieval.pgvector_store import RlsPgVectorVectorStore, vector_table_ddl

_NAMESPACE = "t/t-1/w/w-1"
_WIDTH = 128
_SET = EmbeddingSetBinding(set_id="es-aa", provider="azure", model="dep", dimensions=_WIDTH)


def _store() -> RlsPgVectorVectorStore:
    return RlsPgVectorVectorStore(
        database_url="postgresql+asyncpg://u:p@h/db",
        dimension=_WIDTH,
        table_name="canon_chunk_vectors",
    )


def _async_cm(value: object = None) -> MagicMock:
    """A ``MagicMock`` usable as ``async with``."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _connected(store: RlsPgVectorVectorStore) -> AsyncMock:
    """Give ``store`` a pool whose connection is a recording mock."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_async_cm())
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_async_cm(conn))
    store._pool = pool
    store._initialised = True
    return conn


def _document(doc_id: str = "c1", width: int = _WIDTH) -> VectorDocument:
    return VectorDocument(id=doc_id, text="hello", embedding=[0.1] * width, metadata={"page": "1"})


class TestLazyTableShape:
    def test_the_lazily_created_table_is_already_set_shaped(self) -> None:
        rendered = "\n".join(vector_table_ddl("canon_chunk_vectors"))
        assert "embedding  vector NOT NULL" in rendered
        assert "set_id     TEXT NOT NULL" in rendered
        assert "dim        INTEGER NOT NULL" in rendered
        assert "model      TEXT NOT NULL" in rendered
        assert "PRIMARY KEY (set_id, id)" in rendered

    def test_no_global_ann_index_is_created(self) -> None:
        """There is one ANN index per SET, built when the set is written.

        pgvector refuses a plain HNSW on an untyped ``vector`` column anyway
        (``column does not have dimensions``).
        """
        rendered = "\n".join(vector_table_ddl("canon_chunk_vectors"))
        assert "hnsw" not in rendered.lower()


class TestBindingIsMandatory:
    async def test_an_unbound_upsert_is_refused(self) -> None:
        with pytest.raises(VectorStoreError, match="no embedding set is bound"):
            await _store()._upsert([_document()], _NAMESPACE)

    async def test_an_unbound_search_is_refused(self) -> None:
        with pytest.raises(VectorStoreError, match="no embedding set is bound"):
            await _store()._search([0.1] * 4, 5, _NAMESPACE, None)

    async def test_a_binding_outside_the_supported_width_is_refused(self) -> None:
        silly = EmbeddingSetBinding(set_id="es-x", provider="p", model="m", dimensions=99999)
        with pytest.raises(VectorStoreError, match="outside the supported range"), bind_embedding_set(silly):
            await _store()._search([0.1], 5, _NAMESPACE, None)


class TestUpsert:
    async def test_it_writes_the_set_the_width_and_the_model(self) -> None:
        store = _store()
        conn = _connected(store)
        with bind_embedding_set(_SET):
            await store._upsert([_document()], _NAMESPACE)
        sql, rows = conn.executemany.await_args.args
        assert "(id, set_id, namespace, embedding, dim, model, text, metadata)" in " ".join(sql.split())
        assert rows[0][1] == "es-aa"
        assert rows[0][4] == _WIDTH
        assert rows[0][5] == "azure:dep"

    async def test_the_conflict_target_is_the_composite_key(self) -> None:
        """``ON CONFLICT (id)`` would make one chunk's two sets collide, and a
        replayed reindex batch is only idempotent because of this."""
        store = _store()
        conn = _connected(store)
        with bind_embedding_set(_SET):
            await store._upsert([_document()], _NAMESPACE)
        sql = " ".join(conn.executemany.await_args.args[0].split())
        assert "ON CONFLICT (set_id, id) DO UPDATE" in sql

    async def test_the_scope_guc_is_set_before_the_write(self) -> None:
        store = _store()
        conn = _connected(store)
        with bind_embedding_set(_SET):
            await store._upsert([_document()], _NAMESPACE)
        first = str(conn.execute.await_args_list[0].args[0])
        assert "set_config('app.scope_namespace'" in first

    async def test_a_vector_of_the_wrong_width_is_refused_before_it_is_written(self) -> None:
        """The column has no typmod now, so this check has to live here.

        A provider that ignores ``dimensions=`` would otherwise land a
        wrong-width row in the set and break its index build.
        """
        store = _store()
        conn = _connected(store)
        with pytest.raises(VectorStoreError) as exc, bind_embedding_set(_SET):
            await store._upsert([_document(width=7)], _NAMESPACE)
        assert "returned 7 dimensions" in str(exc.value)
        conn.executemany.assert_not_awaited()

    async def test_a_document_without_an_embedding_is_refused(self) -> None:
        store = _store()
        _connected(store)
        document = VectorDocument(id="c1", text="x", embedding=None, metadata={})
        with pytest.raises(VectorStoreError, match="has no embedding"), bind_embedding_set(_SET):
            await store._upsert([document], _NAMESPACE)


class TestSearch:
    async def test_the_set_predicate_and_the_cast_match_the_index_expression(self) -> None:
        """Character for character, or the planner drops to a sequential scan."""
        store = _store()
        conn = _connected(store)
        conn.fetch.return_value = []
        with bind_embedding_set(_SET):
            await store._search([0.1] * _WIDTH, 5, _NAMESPACE, None)
        sql = " ".join(conn.fetch.await_args.args[0].split())
        assert "WHERE namespace = $2 AND set_id = $4" in sql
        assert f"ORDER BY embedding::vector({_WIDTH}) <=> $1::vector({_WIDTH})" in sql
        assert conn.fetch.await_args.args[4] == "es-aa"
        # ...and the index the store would build casts the same way.
        assert f"(embedding::vector({_WIDTH})) vector_cosine_ops" in store.index_statement(_SET)
        assert "WHERE set_id = 'es-aa'" in store.index_statement(_SET)

    async def test_metadata_filters_are_numbered_after_the_set(self) -> None:
        from fireflyframework_agentic.vectorstores.types import SearchFilter

        store = _store()
        conn = _connected(store)
        conn.fetch.return_value = []
        with bind_embedding_set(_SET):
            await store._search(
                [0.1] * _WIDTH, 5, _NAMESPACE, [SearchFilter(field="page", operator="eq", value="1")]
            )
        sql = " ".join(conn.fetch.await_args.args[0].split())
        assert "metadata ->> $5 = $6" in sql

    async def test_iterative_scan_is_relaxed_so_a_small_workspace_is_findable(self) -> None:
        """The namespace filter is applied AFTER the ANN scan, so a workspace
        of nine chunks inside a set of a million can under-recall at the
        default ``off``."""
        store = _store()
        conn = _connected(store)
        conn.fetch.return_value = []
        with bind_embedding_set(_SET):
            await store._search([0.1] * _WIDTH, 5, _NAMESPACE, None)
        executed = [str(call.args[0]) for call in conn.execute.await_args_list]
        assert any("hnsw.ef_search" in s for s in executed)
        assert any("hnsw.iterative_scan = relaxed_order" in s for s in executed)

    async def test_a_query_embedded_at_another_width_is_refused(self) -> None:
        """A query must be embedded by the model that produced the corpus.

        Without this the cast would raise deep inside pgvector with a message
        about dimensions and nothing about which model was wrong.
        """
        store = _store()
        with pytest.raises(VectorStoreError) as exc, bind_embedding_set(_SET):
            await store._search([0.1] * 768, 5, _NAMESPACE, None)
        assert "embedded by the model that produced the corpus" in str(exc.value)


class TestDelete:
    async def test_a_source_delete_purges_every_set(self) -> None:
        """An erasure claim has to be true of the retired set too.

        The write path is set-scoped; a vector left behind in a retired or
        half-built set is a document the operator believes is gone and the RLM
        corpus can still read.
        """
        store = _store()
        conn = _connected(store)
        await store._delete(["c1", "c2"], _NAMESPACE)
        sql = " ".join(str(conn.execute.await_args.args[0]).split())
        assert "set_id" not in sql
        assert "WHERE namespace = $1 AND id = ANY($2::text[])" in sql


class TestIndexCreation:
    async def test_a_role_that_may_not_create_the_index_warns_with_the_statement(self, caplog) -> None:
        """``CREATE INDEX`` demands ownership, which ``flycanon_app`` lacks.

        Failing an ingest over it would be worse than serving that set from a
        sequential scan -- but an operator has to be told, with the statement.
        """
        store = _store()
        conn = _connected(store)
        conn.execute.side_effect = RuntimeError("permission denied for table canon_chunk_vectors")
        with caplog.at_level("WARNING"):
            assert await store.ensure_set_index(_SET) is False
        assert "CREATE INDEX IF NOT EXISTS canon_chunk_vectors_hnsw_es_aa" in caplog.text

    async def test_the_index_is_attempted_once_per_process(self) -> None:
        store = _store()
        conn = _connected(store)
        assert await store.ensure_set_index(_SET) is True
        assert await store.ensure_set_index(_SET) is True
        assert conn.execute.await_count == 1
