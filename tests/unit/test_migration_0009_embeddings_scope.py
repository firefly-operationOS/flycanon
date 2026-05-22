# Copyright 2026 Firefly Software Solutions Inc
"""Smoke test for migration 0009 ``embeddings_scope`` (canon_chunks side)."""

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
    return f"sqlite:///{tmp_path / 'test_0009.db'}"


def _indexes(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {i["name"]: i for i in sa.inspect(engine).get_indexes(table)}


def test_upgrade_swaps_reembed_index(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0009_embeddings_scope")
    engine = sa.create_engine(sqlite_url)
    idxs = _indexes(engine, "canon_chunks")
    assert "ix_canon_chunks_tenant_workspace_model" in idxs
    new = idxs["ix_canon_chunks_tenant_workspace_model"]
    assert new["column_names"] == ["tenant_id", "workspace_id", "embedding_model"]
    assert "ix_canon_chunks_source_model" not in idxs


def test_upgrade_adds_scope_source_composite(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0009_embeddings_scope")
    engine = sa.create_engine(sqlite_url)
    idxs = _indexes(engine, "canon_chunks")
    assert "ix_canon_chunks_scope_source" in idxs
    assert idxs["ix_canon_chunks_scope_source"]["column_names"] == [
        "tenant_id",
        "workspace_id",
        "source_id",
        "index_in_source",
    ]


def test_downgrade_restores_reembed_index(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0009_embeddings_scope")
    command.downgrade(cfg, "0008_workspaces")
    engine = sa.create_engine(sqlite_url)
    idxs = _indexes(engine, "canon_chunks")
    assert "ix_canon_chunks_source_model" in idxs
    assert "ix_canon_chunks_tenant_workspace_model" not in idxs
    assert "ix_canon_chunks_scope_source" not in idxs
