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

"""Smoke test for migration 0008 ``workspaces_and_multitenancy``."""

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
    return f"sqlite:///{tmp_path / 'test_0008.db'}"


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


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


def test_upgrade_creates_canon_workspaces(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0008_workspaces")
    engine = sa.create_engine(sqlite_url)
    cols = _columns(engine, "canon_workspaces")
    expected = {
        "id",
        "tenant_id",
        "name",
        "status",
        "scope_json",
        "sme_roster_json",
        "retention_days",
        "jurisdiction",
        "created_at",
        "updated_at",
        "closed_at",
    }
    assert expected <= cols


def test_upgrade_adds_tenant_workspace_to_all_canon_tables(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0008_workspaces")
    engine = sa.create_engine(sqlite_url)
    for table in _TENANT_WORKSPACE_TABLES:
        cols = _columns(engine, table)
        assert "tenant_id" in cols, f"{table} missing tenant_id"
        assert "workspace_id" in cols, f"{table} missing workspace_id"


def test_upgrade_backfills_existing_rows_to_default(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0007_cost_events")
    engine = sa.create_engine(sqlite_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO canon_sources ("
                " id, kind, status, content_sha256, content_bytes,"
                " n_chunks, metadata_json, created_at"
                ") VALUES ("
                " 'src-1', 'pdf', 'ingested', 'hash-1', 0,"
                " 0, '{}', CURRENT_TIMESTAMP"
                ")"
            )
        )
    command.upgrade(cfg, "0008_workspaces")
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT tenant_id, workspace_id FROM canon_sources WHERE id='src-1'")
        ).one()
    assert row.tenant_id == "default"
    assert row.workspace_id == "default"


def test_downgrade_removes_columns_and_table(sqlite_url: str) -> None:
    cfg = _alembic_cfg(sqlite_url)
    command.upgrade(cfg, "0008_workspaces")
    command.downgrade(cfg, "0007_cost_events")
    engine = sa.create_engine(sqlite_url)
    tables = sa.inspect(engine).get_table_names()
    assert "canon_workspaces" not in tables
    for table in _TENANT_WORKSPACE_TABLES:
        cols = _columns(engine, table)
        assert "tenant_id" not in cols, f"{table} still has tenant_id after downgrade"
        assert "workspace_id" not in cols, f"{table} still has workspace_id after downgrade"
