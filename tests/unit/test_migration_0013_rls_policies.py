# Copyright 2026 Firefly Software Solutions Inc
"""Smoke test for migration ``0013_rls_policies``.

Postgres-only behavior is verified by ``tests/integration/test_rls_isolation.py``
(testcontainer-backed). This test confirms the migration applies cleanly
to a fresh SQLite DB -- where it is a no-op -- and doesn't break
``alembic upgrade head`` / ``alembic downgrade``.
"""

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
    return f"sqlite:///{tmp_path / 'test_0013.db'}"


def test_upgrade_to_0013_is_clean_on_sqlite(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0013_rls_policies")


def test_downgrade_from_0013_is_clean_on_sqlite(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0013_rls_policies")
    command.downgrade(cfg, "0012_agent_tokens")
