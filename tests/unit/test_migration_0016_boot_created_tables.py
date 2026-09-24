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

"""Migration ``0016_boot_created_tables``: shape parity and the SQLite no-op.

The migration restates the framework's ``canon_chunk_vectors`` DDL (the
framework keeps it as f-strings inside ``PgVectorVectorStore._create_schema``,
nothing importable), so the one thing that must never drift -- the column set
and the index parameters -- is pinned here against the framework's OWN output,
captured from a recording connection. The outbox tables are imported from
PyFly's constants and need no such guard.

The behavioural half (a NOBYPASSRLS role boots the dense store on a database
this migration prepared, with no DDL of its own) lives in
``tests/integration/test_search_under_app_role.py``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fireflyframework_agentic.vectorstores import PgVectorVectorStore

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "migrations" / "versions" / "20260917_1500_0016_boot_created_tables.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0016", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns_of(create_table_sql: str) -> dict[str, str]:
    """``{column: type}`` from a ``CREATE TABLE`` statement, defaults stripped."""
    body = create_table_sql[create_table_sql.index("(") + 1 : create_table_sql.rindex(")")]
    columns: dict[str, str] = {}
    for line in body.splitlines():
        match = re.match(r"\s*(\w+)\s+([A-Za-z]+(?:\(\d+\))?)", line)
        if match:
            columns[match.group(1)] = match.group(2)
    return columns


async def _framework_statements(dimension: int, *, hnsw_m: int, hnsw_ef_construction: int) -> list[str]:
    store = PgVectorVectorStore(
        "postgresql://u:p@h/db",
        dimension=dimension,
        table_name="canon_chunk_vectors",
        hnsw_m=hnsw_m,
        hnsw_ef_construction=hnsw_ef_construction,
    )
    conn = AsyncMock()
    await store._create_schema(conn)
    return [str(call.args[0]) for call in conn.execute.await_args_list]


@pytest.mark.parametrize("dimension", [3, 768, 1536])
async def test_vector_table_ddl_matches_the_framework_column_for_column(dimension: int) -> None:
    migration = _load_migration()
    ours = migration.vector_table_ddl(
        table="canon_chunk_vectors", dimension=dimension, hnsw_m=24, hnsw_ef_construction=96
    )
    theirs = await _framework_statements(dimension, hnsw_m=24, hnsw_ef_construction=96)

    our_create = next(s for s in ours if "CREATE TABLE" in s)
    their_create = next(s for s in theirs if "CREATE TABLE" in s)
    assert _columns_of(our_create) == _columns_of(their_create)
    assert f"vector({dimension})" in our_create

    # Same index names, same HNSW build parameters, same extension.
    def _normalise(statement: str) -> str:
        return " ".join(statement.split())

    for needle in (
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE INDEX IF NOT EXISTS canon_chunk_vectors_hnsw ON canon_chunk_vectors USING hnsw "
        "(embedding vector_cosine_ops) WITH (m = 24, ef_construction = 96)",
        "CREATE INDEX IF NOT EXISTS canon_chunk_vectors_namespace ON canon_chunk_vectors (namespace)",
    ):
        assert any(needle in _normalise(s) for s in ours), needle
        assert any(needle in _normalise(s) for s in theirs), needle


def test_vector_table_gets_the_forced_namespace_policy() -> None:
    migration = _load_migration()
    rendered = "\n".join(
        migration.vector_table_ddl(
            table="canon_chunk_vectors", dimension=768, hnsw_m=16, hnsw_ef_construction=64
        )
    )
    assert "ENABLE ROW LEVEL SECURITY" in rendered
    assert "FORCE ROW LEVEL SECURITY" in rendered
    assert "tenant_workspace_isolation" in rendered
    assert "current_setting('app.scope_namespace', true)" in rendered
    assert "WITH CHECK" in rendered


def test_outbox_ddl_is_pyflys_own() -> None:
    from pyfly.eda.adapters.postgres import _DDL_OFFSETS, _DDL_OUTBOX

    source = _MIGRATION.read_text(encoding="utf-8")
    assert "from pyfly.eda.adapters.postgres import _DDL_OFFSETS, _DDL_OUTBOX" in source
    assert "op.execute(_DDL_OUTBOX)" in source and "op.execute(_DDL_OFFSETS)" in source
    # ... and those constants still create what start() expects to find.
    assert "CREATE TABLE IF NOT EXISTS pyfly_eda_outbox" in _DDL_OUTBOX
    assert "CREATE TABLE IF NOT EXISTS pyfly_eda_offsets" in _DDL_OFFSETS


def test_revision_follows_0015_and_is_followed_by_0017() -> None:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("0016_boot_created_tables").down_revision == "0015_source_object_store_key"
    # 0017 reshapes the table 0016 creates, so the order of the two is part
    # of this migration's contract, not an incidental fact about the head.
    assert script.get_revision("0017_embedding_sets").down_revision == "0016_boot_created_tables"


def test_sqlite_upgrade_and_downgrade_are_no_ops(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'test_0016.db'}"
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(sa.inspect(sa.create_engine(url)).get_table_names())
    assert "canon_chunks" in tables
    assert not {"canon_chunk_vectors", "pyfly_eda_outbox", "pyfly_eda_offsets"} & tables
    command.downgrade(cfg, "0015_source_object_store_key")
