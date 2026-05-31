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

"""Smoke test for migration 0012 ``agent_tokens``.

The migration introduces ``canon_agent_tokens`` -- the tenant-scoped
agent token store that protects the upcoming ``/api/v1/agent/*``
surface. The test verifies the upgrade creates the table with every
expected column + the two indexes (unique on ``prefix``, composite on
``(tenant_id, revoked_at)``), and that the downgrade fully removes
the table.
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
    return f"sqlite:///{tmp_path / 'test_0012.db'}"


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_upgrade_creates_agent_tokens(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0012_agent_tokens")
    engine = sa.create_engine(sqlite_url)
    cols = _columns(engine, "canon_agent_tokens")
    expected = {
        "id",
        "tenant_id",
        "name",
        "prefix",
        "secret_hash",
        "workspace_allowlist_json",
        "scopes_json",
        "rate_limit_rpm",
        "expires_at",
        "created_at",
        "created_by",
        "revoked_at",
        "last_used_at",
    }
    assert expected <= cols


def test_unique_index_on_prefix(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0012_agent_tokens")
    engine = sa.create_engine(sqlite_url)
    indexes = sa.inspect(engine).get_indexes("canon_agent_tokens")
    by_name = {i["name"]: i for i in indexes}
    assert "uq_canon_agent_tokens_prefix" in by_name
    assert by_name["uq_canon_agent_tokens_prefix"]["unique"]


def test_composite_index_on_tenant_revoked(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0012_agent_tokens")
    engine = sa.create_engine(sqlite_url)
    indexes = sa.inspect(engine).get_indexes("canon_agent_tokens")
    by_name = {i["name"]: i for i in indexes}
    assert "ix_canon_agent_tokens_tenant_active" in by_name
    assert by_name["ix_canon_agent_tokens_tenant_active"]["column_names"] == [
        "tenant_id",
        "revoked_at",
    ]


def test_downgrade_drops_table(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0012_agent_tokens")
    command.downgrade(cfg, "0011_tighten_scope")
    engine = sa.create_engine(sqlite_url)
    tables = sa.inspect(engine).get_table_names()
    assert "canon_agent_tokens" not in tables
