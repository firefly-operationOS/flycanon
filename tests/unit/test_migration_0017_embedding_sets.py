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

"""Migration ``0017_embedding_sets``: the DDL shape, and the portable half.

What the migration does to a POPULATED pgvector table -- adopt, relax, re-key,
rebuild, and refuse a two-width downgrade -- is proven against a live server in
``tests/integration/test_migration_0017_embedding_sets.py``. What is pinned
here is the shape of the statements and the fact that the portable half runs
on SQLite, which is where the unit suite's schema comes from.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "migrations" / "versions" / "20260924_1200_0017_embedding_sets.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0017", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalise(statement: str) -> str:
    return " ".join(statement.split())


def _cfg(url: str | None = None) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_0017_is_the_head_and_follows_0016() -> None:
    script = ScriptDirectory.from_config(_cfg())
    assert script.get_current_head() == "0017_embedding_sets"
    assert script.get_revision("0017_embedding_sets").down_revision == "0016_boot_created_tables"


def test_the_registry_carries_the_standard_scoped_rls_policy() -> None:
    """Migration 0013's family, so a new table is not a new isolation story."""
    rendered = "\n".join(_load_migration().embedding_sets_ddl())
    assert "ENABLE ROW LEVEL SECURITY" in rendered
    assert "FORCE ROW LEVEL SECURITY" in rendered
    assert "tenant_workspace_isolation" in rendered
    assert "current_setting('app.tenant_id', true)" in rendered
    assert "current_setting('app.workspace_id', true)" in rendered


def test_the_registry_has_no_unique_on_the_configuration_fingerprint() -> None:
    """Re-embedding onto the IDENTICAL configuration must stay possible.

    It is what a deployment does after a batch was written badly. A UNIQUE
    (tenant, workspace, fingerprint) would forbid exactly the case the set id
    exists to express.
    """
    rendered = _normalise("\n".join(_load_migration().embedding_sets_ddl()))
    assert "UNIQUE" not in rendered.upper()
    assert "CHECK (dimensions BETWEEN 64 AND 4096)" in rendered


def test_the_partial_index_is_keyed_by_set_and_casts_to_the_set_width() -> None:
    """Per SET, not per width.

    A per-width index would force a dual-set window at one width into filtered
    ANN across both sets -- a recall hazard precisely during a switch. And the
    cast is what gives an untyped ``vector`` column dimensions at all.
    """
    statement = _normalise(
        _load_migration().partial_hnsw_ddl(
            set_id="es-abc", dimensions=3072, hnsw_m=24, hnsw_ef_construction=96
        )
    )
    assert "canon_chunk_vectors_hnsw_es_abc" in statement
    assert "USING hnsw ((embedding::vector(3072)) vector_cosine_ops)" in statement
    assert "WITH (m = 24, ef_construction = 96)" in statement
    assert "WHERE set_id = 'es-abc'" in statement


def test_the_coherence_trigger_names_the_command_that_does_it_properly() -> None:
    """The guard that closes the silent-corruption hole.

    A message that only says "no" leaves an operator to guess; this one says
    what a new embedding space is created with.
    """
    rendered = "\n".join(_load_migration().set_coherence_ddl())
    assert "SECURITY DEFINER" in rendered
    assert "SET search_path = pg_catalog, public" in rendered
    assert "BEFORE INSERT OR UPDATE ON canon_chunk_vectors" in rendered
    assert "flycanon reindex" in rendered
    assert "ONE embedding space" in rendered
    # A vector whose set does not exist is itself the bug this catches.
    assert "names no row in canon_embedding_sets" in rendered


def test_sqlite_gets_the_portable_half_and_skips_pgvector(tmp_path: Path) -> None:
    """The unit suite's schema comes through here.

    ``canon_embedding_sets`` arrives from the ORM metadata on SQLite; what the
    migration has to do is the three portable ALTERs, and it has to do them
    both ways.
    """
    url = f"sqlite:///{tmp_path / 'test_0017.db'}"
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)

    jobs = {c["name"] for c in inspector.get_columns("canon_ingest_jobs")}
    assert "kind" in jobs
    workspaces = {c["name"] for c in inspector.get_columns("canon_workspaces")}
    assert "active_embedding_set_id" in workspaces
    chunks = {c["name"] for c in inspector.get_columns("canon_chunks")}
    assert "embedding" not in chunks, "the column that made the dashboard lie is gone"
    assert "embedding_model" in chunks, "the column that answers 'what produced you?' stays"
    assert "canon_chunk_vectors" not in set(inspector.get_table_names())

    command.downgrade(cfg, "0016_boot_created_tables")
    inspector = sa.inspect(sa.create_engine(url))
    assert "kind" not in {c["name"] for c in inspector.get_columns("canon_ingest_jobs")}
    assert "active_embedding_set_id" not in {c["name"] for c in inspector.get_columns("canon_workspaces")}
    assert "embedding" in {c["name"] for c in inspector.get_columns("canon_chunks")}
    engine.dispose()


def test_existing_ingest_jobs_default_to_the_ingest_kind(tmp_path: Path) -> None:
    """Every row written before 26.8.0 is an ingest, and says so."""
    url = f"sqlite:///{tmp_path / 'test_0017_default.db'}"
    command.upgrade(_cfg(url), "head")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO canon_ingest_jobs (id, tenant_id, workspace_id, status, attempts, "
                "created_at, updated_at) VALUES ('j1', 't1', 'w1', 'queued', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        kind = conn.execute(sa.text("SELECT kind FROM canon_ingest_jobs WHERE id = 'j1'")).scalar_one()
    assert kind == "ingest"
    engine.dispose()
