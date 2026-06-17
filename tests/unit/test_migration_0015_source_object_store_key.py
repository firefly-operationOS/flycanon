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

"""Smoke test for migration 0015 ``source_object_store_key``.

The migration adds the nullable ``object_store_key`` column to
``canon_sources`` -- the ObjectStore key of the persisted original
document for RLM. The test verifies the upgrade adds the column and
that the downgrade removes it again, plus that the mapped model exposes
the attribute defaulting to ``None``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from flycanon.models.entities.source import SourceRow


def _alembic_cfg(db_url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test_0015.db'}"


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_upgrade_adds_object_store_key(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0015_source_object_store_key")
    engine = sa.create_engine(sqlite_url)
    assert "object_store_key" in _columns(engine, "canon_sources")


def test_object_store_key_is_nullable(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0015_source_object_store_key")
    engine = sa.create_engine(sqlite_url)
    cols = {c["name"]: c for c in sa.inspect(engine).get_columns("canon_sources")}
    assert cols["object_store_key"]["nullable"]


def test_downgrade_drops_object_store_key(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0015_source_object_store_key")
    command.downgrade(cfg, "0014_drop_legacy_chunk_vectors")
    engine = sa.create_engine(sqlite_url)
    assert "object_store_key" not in _columns(engine, "canon_sources")


def test_model_exposes_object_store_key_defaulting_none() -> None:
    assert "object_store_key" in SourceRow.__table__.columns
    row = SourceRow(
        tenant_id="default",
        workspace_id="default",
        kind="unknown",
        status="pending",
        content_sha256="0" * 64,
    )
    assert row.object_store_key is None
