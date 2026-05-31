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

"""Hermetic coverage for :class:`RlsPgVectorVectorStore`.

The generic pgvector adapter lives in the framework; flycanon's subclass adds
only the Postgres Row-Level Security coupling:

* :meth:`_prepare_session` sets the ``app.scope_namespace`` GUC (transaction
  local) from the scope namespace the :class:`TenantScopedVectorStore` encodes.
* :meth:`_create_schema` installs a namespace-keyed, ``FORCE``\\ d RLS policy.

These tests mock the asyncpg connection so they need no database. The
behavioural ANN + RLS isolation against a real Postgres + pgvector container
lives in ``tests/integration/test_rls_isolation.py``. The framework's own
scope-contract tests (required ``tenant_id`` / ``workspace_id``) live in
``fireflyframework_agentic`` ``tests/unit/vectorstores/test_scoped.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from fireflyframework_agentic.vectorstores import PgVectorVectorStore

from flycanon.core.services.retrieval.pgvector_store import RlsPgVectorVectorStore, _asyncpg_dsn


def test_is_generic_pgvector_subclass() -> None:
    assert issubclass(RlsPgVectorVectorStore, PgVectorVectorStore)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
    ],
)
def test_asyncpg_dsn_strips_sqlalchemy_driver(url: str, expected: str) -> None:
    assert _asyncpg_dsn(url) == expected


def test_constructor_maps_flycanon_settings() -> None:
    store = RlsPgVectorVectorStore(
        database_url="postgresql+asyncpg://u:p@h/db",
        dimension=1536,
        table_name="canon_chunk_vectors",
        hnsw_m=16,
        hnsw_ef_construction=64,
        hnsw_ef_search=200,
    )
    assert store._url == "postgresql://u:p@h/db"
    assert store._dimension == 1536
    assert store._table == "canon_chunk_vectors"
    assert store._hnsw_ef_search == 200


class TestRlsHooks:
    async def test_prepare_session_sets_scope_namespace_guc(self) -> None:
        store = RlsPgVectorVectorStore(database_url="postgresql://u:p@h/db", dimension=4)
        conn = AsyncMock()
        await store._prepare_session(conn, namespace="t/acme/w/ws-a")
        conn.execute.assert_awaited_once()
        sql, value = conn.execute.await_args.args
        assert "set_config" in sql
        assert "app.scope_namespace" in sql
        assert value == "t/acme/w/ws-a"

    async def test_create_schema_installs_namespace_keyed_forced_rls(self) -> None:
        store = RlsPgVectorVectorStore(
            database_url="postgresql://u:p@h/db", dimension=4, table_name="canon_chunk_vectors"
        )
        conn = AsyncMock()
        await store._create_schema(conn)
        executed = "\n".join(str(call.args[0]) for call in conn.execute.await_args_list)
        # Base schema (extension/table) still created...
        assert "CREATE EXTENSION IF NOT EXISTS vector" in executed
        assert "canon_chunk_vectors" in executed
        # ...plus the flycanon RLS policy, keyed on the scope namespace, FORCEd.
        assert "tenant_workspace_isolation" in executed
        assert "app.scope_namespace" in executed
        assert "FORCE ROW LEVEL SECURITY" in executed
