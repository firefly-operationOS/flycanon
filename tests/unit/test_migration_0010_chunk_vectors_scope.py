# Copyright 2026 Firefly Software Solutions Inc
"""Smoke test for migration 0010 ``chunk_vectors_scope``."""

from __future__ import annotations

from pathlib import Path

import pytest
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
    return f"sqlite:///{tmp_path / 'test_0010.db'}"


def test_upgrade_is_idempotent_on_sqlite(sqlite_url: str) -> None:
    """SQLite has no canon_chunk_vectors table (pgvector-only).
    Migration should detect absence and no-op gracefully."""
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0010_chunk_vectors_scope")


def test_downgrade_is_noop_on_sqlite(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0010_chunk_vectors_scope")
    command.downgrade(cfg, "0009_embeddings_scope")
