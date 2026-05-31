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
