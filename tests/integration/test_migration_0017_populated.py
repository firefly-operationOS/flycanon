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

"""Migration ``0017`` against a POPULATED pgvector table.

There is no production data in flycanon anywhere -- the only corpus that
exists is a dev stack's throwaway index -- so on every deployment that exists
today 0017 is pure DDL. That is precisely why this module has to exist: the
adoption path is the only thing standing between a future populated
deployment and a fresh database, and it will not be exercised by anything
else until the day it matters.

What is proven here, on a table seeded in the pre-26.8.0 ``vector(N)`` shape:

* **The upgrade loses no row.** N rows in, N rows out, ``dim`` measured per
  row by ``vector_dims`` rather than assumed, ``set_id`` and ``model``
  backfilled, the primary key re-keyed and one partial HNSW per adopted set.
* **The workspace ends up pointing at the set its vectors are in**, which is
  what makes the corpus searchable after the migration rather than merely
  present.
* **The downgrade round-trips** at one width -- ``vector(N)`` again, same row
  count.
* **The downgrade REFUSES at two widths** rather than truncating rows into a
  width they were not embedded at, and names ``flycanon reindex --drop-set``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

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
#: The width the seeded corpus was embedded at -- the dworkers dev stack's
#: ``ollama:nomic-embed-text``, scaled down to keep the fixture quick.
WIDTH = 64
ROWS = 40
TENANT = "t-dev"
WORKSPACES = ("w-alpha", "w-beta")


def _cfg(url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


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


def _literal(width: int, axis: int) -> str:
    return "[" + ",".join("1.0" if i == axis else "0.0" for i in range(width)) + "]"


@pytest.fixture
def seeded() -> Iterator[tuple[PostgresContainer, sa.Engine]]:
    """A database at migration 0016 with a corpus already in it.

    Function-scoped and one container per test: these tests move the schema
    forwards and backwards, so sharing one would make them order-dependent in
    exactly the way a migration test must not be.
    """
    from flycanon.config import get_settings

    previous = os.environ.get("FLYCANON_EMBEDDING_DIMENSIONS")
    previous_model = os.environ.get("FLYCANON_EMBEDDING_MODEL")
    os.environ["FLYCANON_EMBEDDING_DIMENSIONS"] = str(WIDTH)
    os.environ["FLYCANON_EMBEDDING_MODEL"] = "ollama:nomic-embed-text"
    get_settings.cache_clear()
    try:
        with PostgresContainer(_PGVECTOR_IMAGE) as pg:
            url = _async(pg.get_connection_url())
            # Stop at 0016: this is the pre-26.8.0 world the corpus lives in.
            command.upgrade(_cfg(url), "0016_boot_created_tables")
            engine = sa.create_engine(_sync(url), future=True)
            with engine.begin() as conn:
                for workspace in WORKSPACES:
                    conn.execute(
                        sa.text(
                            "INSERT INTO canon_workspaces (id, tenant_id, name, status) "
                            "VALUES (:id, :tenant, :id, 'active')"
                        ),
                        {"id": workspace, "tenant": TENANT},
                    )
                for index in range(ROWS):
                    workspace = WORKSPACES[index % len(WORKSPACES)]
                    conn.execute(
                        sa.text(
                            "INSERT INTO canon_chunk_vectors (id, namespace, embedding, text) "
                            "VALUES (:id, :ns, CAST(:vec AS vector), :text)"
                        ),
                        {
                            "id": f"chunk-{index:03d}",
                            "ns": f"t/{TENANT}/w/{workspace}",
                            "vec": _literal(WIDTH, index % WIDTH),
                            "text": f"document {index}",
                        },
                    )
            try:
                yield pg, engine
            finally:
                engine.dispose()
    finally:
        for name, value in (
            ("FLYCANON_EMBEDDING_DIMENSIONS", previous),
            ("FLYCANON_EMBEDDING_MODEL", previous_model),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()


def _upgrade(pg: PostgresContainer) -> None:
    command.upgrade(_cfg(_async(pg.get_connection_url())), "head")


def _downgrade(pg: PostgresContainer) -> None:
    command.downgrade(_cfg(_async(pg.get_connection_url())), "0016_boot_created_tables")


class TestUpgrade:
    def test_it_starts_from_the_pre_26_8_0_shape(self, seeded) -> None:
        """The control: without this the rest would prove nothing."""
        _pg, engine = seeded
        with engine.connect() as conn:
            column_type = conn.execute(
                sa.text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_class c "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'embedding' "
                    "WHERE c.relname = 'canon_chunk_vectors'"
                )
            ).scalar_one()
            count = conn.execute(sa.text("SELECT count(*) FROM canon_chunk_vectors")).scalar_one()
        assert column_type == f"vector({WIDTH})"
        assert count == ROWS

    def test_no_row_is_lost_and_every_width_is_measured(self, seeded) -> None:
        pg, engine = seeded
        _upgrade(pg)
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT count(*) AS rows, count(DISTINCT dim) AS widths, min(dim) AS width, "
                    "count(*) FILTER (WHERE set_id IS NULL) AS orphans, "
                    "count(*) FILTER (WHERE dim <> vector_dims(embedding)) AS mismatched "
                    "FROM canon_chunk_vectors"
                )
            ).one()
        assert row.rows == ROWS
        assert row.widths == 1
        assert row.width == WIDTH
        assert row.orphans == 0
        # ``dim`` is measured with vector_dims per row, never assumed from a
        # setting -- which is what makes the backfill self-checking.
        assert row.mismatched == 0

    def test_one_set_per_workspace_labelled_with_the_migrate_job_s_model(self, seeded) -> None:
        pg, engine = seeded
        _upgrade(pg)
        with engine.connect() as conn:
            sets = conn.execute(
                sa.text(
                    "SELECT workspace_id, provider, model, dimensions, status, vector_count "
                    "FROM canon_embedding_sets ORDER BY workspace_id"
                )
            ).all()
        assert [s.workspace_id for s in sets] == list(WORKSPACES)
        assert {s.provider for s in sets} == {"ollama"}
        assert {s.model for s in sets} == {"nomic-embed-text"}
        assert {s.dimensions for s in sets} == {WIDTH}
        assert {s.status for s in sets} == {"active"}
        assert sum(s.vector_count for s in sets) == ROWS

    def test_each_workspace_points_at_the_set_its_vectors_are_in(self, seeded) -> None:
        """Without this the corpus survives the migration but stops being
        searchable, which would be a worse outcome than losing it loudly."""
        pg, engine = seeded
        _upgrade(pg)
        with engine.connect() as conn:
            pairs = conn.execute(
                sa.text(
                    "SELECT w.id, w.active_embedding_set_id, s.workspace_id "
                    "FROM canon_workspaces w "
                    "JOIN canon_embedding_sets s ON s.id = w.active_embedding_set_id "
                    "ORDER BY w.id"
                )
            ).all()
        assert [p.id for p in pairs] == list(WORKSPACES)
        assert all(p.id == p.workspace_id for p in pairs)
        with engine.connect() as conn:
            vectors = conn.execute(
                sa.text(
                    "SELECT DISTINCT v.set_id, w.active_embedding_set_id "
                    "FROM canon_chunk_vectors v "
                    "JOIN canon_workspaces w ON w.id = split_part(v.namespace, '/', 4)"
                )
            ).all()
        assert all(v[0] == v[1] for v in vectors)

    def test_the_table_is_re_keyed_and_re_indexed_per_set(self, seeded) -> None:
        pg, engine = seeded
        _upgrade(pg)
        with engine.connect() as conn:
            primary_key = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'canon_chunk_vectors'::regclass AND contype = 'p'"
                )
            ).scalar_one()
            indexes = list(
                conn.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes WHERE tablename = 'canon_chunk_vectors' "
                        "AND indexdef LIKE '%hnsw%'"
                    )
                ).scalars()
            )
            column_type = conn.execute(
                sa.text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_class c "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'embedding' "
                    "WHERE c.relname = 'canon_chunk_vectors'"
                )
            ).scalar_one()
        assert primary_key == "PRIMARY KEY (set_id, id)"
        assert column_type == "vector", "the typmod is gone; the width lives on the row"
        # The one global HNSW is replaced by one partial index per adopted set.
        assert "canon_chunk_vectors_hnsw" not in indexes
        assert len(indexes) == len(WORKSPACES)

    def test_the_dead_chunk_embedding_column_is_gone(self, seeded) -> None:
        """Nothing ever wrote it, and StatsService counted it -- so the admin
        dashboard reported 0.0% embedded on a fully embedded corpus."""
        pg, engine = seeded
        _upgrade(pg)
        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = 'canon_chunks'"
                    )
                ).scalars()
            )
        assert "embedding" not in columns
        assert "embedding_model" in columns

    def test_an_orphaned_namespace_is_adopted_and_announced(self, seeded) -> None:
        """Vectors whose workspace row is gone.

        Measured on the dworkers dev corpus: one of its 21 namespaces has no
        ``canon_workspaces`` row, so 21 sets were adopted and only 20 pointers
        were set. The rows are worth keeping, but nothing will ever point at
        that set, and an operator counting rows would otherwise have to find
        the discrepancy themselves.
        """
        pg, engine = seeded
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO canon_chunk_vectors (id, namespace, embedding, text) "
                    "VALUES ('orphan-1', :ns, CAST(:vec AS vector), 'orphaned')"
                ),
                {"ns": f"t/{TENANT}/w/w-purged", "vec": _literal(WIDTH, 0)},
            )
        _upgrade(pg)
        # The fact is recorded on the row, not only in migration output that
        # scrolls past: `flycanon reindex --list` is where an operator will be
        # standing when they wonder why a set answers nothing. (Alembic's
        # env.py runs fileConfig(), which takes its loggers off the handler
        # chain, so the WARNING itself is not capturable from here.)
        with engine.connect() as conn:
            note = conn.execute(
                sa.text("SELECT note FROM canon_embedding_sets WHERE workspace_id = 'w-purged'")
            ).scalar_one()
        assert "ORPHANED" in note
        assert "no canon_workspaces row" in note
        with engine.connect() as conn:
            adopted = conn.execute(
                sa.text(
                    "SELECT count(*) FROM canon_chunk_vectors WHERE namespace = :ns AND set_id IS NOT NULL"
                ),
                {"ns": f"t/{TENANT}/w/w-purged"},
            ).scalar_one()
            pointers = conn.execute(
                sa.text("SELECT count(*) FROM canon_workspaces WHERE active_embedding_set_id IS NOT NULL")
            ).scalar_one()
        assert adopted == 1, "the rows are adopted, not dropped"
        assert pointers == len(WORKSPACES), "and no phantom workspace is invented for them"


class TestDowngrade:
    def test_it_round_trips_at_one_width(self, seeded) -> None:
        pg, engine = seeded
        _upgrade(pg)
        _downgrade(pg)
        with engine.connect() as conn:
            column_type = conn.execute(
                sa.text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_class c "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'embedding' "
                    "WHERE c.relname = 'canon_chunk_vectors'"
                )
            ).scalar_one()
            count = conn.execute(sa.text("SELECT count(*) FROM canon_chunk_vectors")).scalar_one()
            primary_key = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'canon_chunk_vectors'::regclass AND contype = 'p'"
                )
            ).scalar_one()
            registry = conn.execute(sa.text("SELECT to_regclass('canon_embedding_sets')")).scalar_one()
        assert column_type == f"vector({WIDTH})"
        assert count == ROWS, "the downgrade is reversible, not lossy"
        assert primary_key == "PRIMARY KEY (id)"
        assert registry is None

    def test_it_refuses_two_widths_rather_than_truncating(self, seeded) -> None:
        """Loud, not lossy.

        A ``vector(N)`` column holds one width; a downgrade that silently
        dropped the other set's rows would be the worst possible behaviour
        here, so the refusal names the command that reduces to one set on
        purpose.
        """
        pg, engine = seeded
        _upgrade(pg)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO canon_embedding_sets (id, tenant_id, workspace_id, provider, "
                    "model, dimensions, status, config_fingerprint) "
                    "VALUES ('es-wide', :tenant, :workspace, 'azure', 'dep', 128, 'ready', 'fp')"
                ),
                {"tenant": TENANT, "workspace": WORKSPACES[0]},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO canon_chunk_vectors (id, set_id, namespace, embedding, dim, "
                    "model, text) VALUES ('wide-1', 'es-wide', :ns, CAST(:vec AS vector), 128, "
                    "'azure:dep', 'wide')"
                ),
                {"ns": f"t/{TENANT}/w/{WORKSPACES[0]}", "vec": _literal(128, 0)},
            )
        with pytest.raises(RuntimeError) as exc:
            _downgrade(pg)
        message = str(exc.value)
        assert "flycanon reindex --drop-set" in message
        assert "Nothing has been changed" in message
        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT count(*) FROM canon_chunk_vectors")).scalar_one()
        assert count == ROWS + 1, "the refusal must not have deleted anything"


class TestEmptyDeployment:
    def test_the_upgrade_is_pure_ddl_on_an_empty_table(self) -> None:
        """The path every deployment that exists today takes."""
        from flycanon.config import get_settings

        get_settings.cache_clear()
        with PostgresContainer(_PGVECTOR_IMAGE) as pg:
            command.upgrade(_cfg(_async(pg.get_connection_url())), "head")
            engine = sa.create_engine(_sync(pg.get_connection_url()), future=True)
            try:
                with engine.connect() as conn:
                    sets = conn.execute(sa.text("SELECT count(*) FROM canon_embedding_sets")).scalar_one()
                    vectors = conn.execute(sa.text("SELECT count(*) FROM canon_chunk_vectors")).scalar_one()
                    column_type = conn.execute(
                        sa.text(
                            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_class c "
                            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'embedding' "
                            "WHERE c.relname = 'canon_chunk_vectors'"
                        )
                    ).scalar_one()
                assert sets == 0
                assert vectors == 0
                assert column_type == "vector"
            finally:
                engine.dispose()
