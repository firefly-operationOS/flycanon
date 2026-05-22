# Copyright 2026 Firefly Software Solutions Inc
"""Smoke test for migration 0011 ``tighten_scope_constraints``.

The migration drops the ``'default'`` server defaults on
``tenant_id`` / ``workspace_id`` across every scoped ``canon_*`` row,
and widens the partial unique index on ``canon_sources.content_sha256``
to include the scope columns. The test exercises both the upgrade
*and* the downgrade so we know the migration is reversible.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _alembic_cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test_0011.db'}"


def _columns(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(engine).get_columns(table)}


def _unique_indexes(engine: sa.Engine, table: str) -> list[dict]:
    """Return only the UNIQUE indexes on ``table``.

    The ``content_sha256`` constraint on ``canon_sources`` is a partial
    UNIQUE INDEX (not a ``UniqueConstraint``), so ``get_unique_constraints``
    returns ``[]``. We have to inspect ``get_indexes`` and filter.
    """
    return [idx for idx in sa.inspect(engine).get_indexes(table) if idx["unique"]]


_TENANT_WORKSPACE_TABLES = [
    "canon_sources",
    "canon_chunks",
    "canon_knowledge_items",
    "canon_knowledge_versions",
    "canon_citations",
    "canon_candidates",
    "canon_knowledge_relations",
    "canon_audit_events",
    "canon_conversations",
    "canon_conversation_turns",
    "canon_ingest_jobs",
    "canon_ingest_job_events",
    "canon_cost_events",
    "canon_taxonomy_nodes",
]


def test_upgrade_drops_default_on_all_canon_scope_columns(sqlite_url: str) -> None:
    """After upgrade, no ``canon_*`` row carries a ``'default'`` fallback."""
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0011_tighten_scope")
    engine = sa.create_engine(sqlite_url)
    for table in _TENANT_WORKSPACE_TABLES:
        cols = _columns(engine, table)
        tenant_default = cols["tenant_id"].get("default")
        workspace_default = cols["workspace_id"].get("default")
        # SQLite reports ``None`` once the default is dropped; on
        # Postgres the inspector returns either ``None`` or an empty
        # string depending on the driver version, so we accept both.
        assert tenant_default in (None, ""), f"{table}.tenant_id still has default={tenant_default!r}"
        assert workspace_default in (None, ""), (
            f"{table}.workspace_id still has default={workspace_default!r}"
        )


def test_upgrade_widens_canon_sources_sha256_unique_index(sqlite_url: str) -> None:
    """The SHA-256 idempotency anchor is now workspace-scoped."""
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0011_tighten_scope")
    engine = sa.create_engine(sqlite_url)
    uniques = _unique_indexes(engine, "canon_sources")
    sha_uniques = [u for u in uniques if "content_sha256" in u["column_names"]]
    assert len(sha_uniques) == 1, f"expected exactly one content_sha256 unique index, got {sha_uniques!r}"
    widened = sha_uniques[0]
    assert widened["name"] == "uq_canon_sources_tenant_workspace_content_sha256"
    assert set(widened["column_names"]) == {
        "tenant_id",
        "workspace_id",
        "content_sha256",
    }


def test_downgrade_restores_defaults_and_narrow_unique(sqlite_url: str) -> None:
    """Rolling back to 0010 puts the schema back exactly as it was."""
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0011_tighten_scope")
    command.downgrade(cfg, "0010_chunk_vectors_scope")
    engine = sa.create_engine(sqlite_url)

    # 1) Server defaults are back on every scoped table. SQLite
    # reports the default as the quoted SQL literal ``"'default'"``.
    for table in _TENANT_WORKSPACE_TABLES:
        cols = _columns(engine, table)
        tenant_default = cols["tenant_id"].get("default")
        workspace_default = cols["workspace_id"].get("default")
        assert tenant_default in ("'default'", "default"), (
            f"{table}.tenant_id default not restored: {tenant_default!r}"
        )
        assert workspace_default in ("'default'", "default"), (
            f"{table}.workspace_id default not restored: {workspace_default!r}"
        )

    # 2) The narrow content_sha256 unique index is back.
    uniques = _unique_indexes(engine, "canon_sources")
    sha_uniques = [u for u in uniques if "content_sha256" in u["column_names"]]
    assert len(sha_uniques) == 1
    assert sha_uniques[0]["name"] == "uq_canon_sources_content_sha256"
    assert sha_uniques[0]["column_names"] == ["content_sha256"]
