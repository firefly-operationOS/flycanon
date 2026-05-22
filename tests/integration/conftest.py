# Copyright 2026 Firefly Software Solutions Inc
"""Shared fixtures for the Postgres + RLS integration suite.

Boots a single ``pgvector/pgvector:pg16`` container for the whole
module, runs ``alembic upgrade head`` as the admin (superuser) role so
the schema + RLS policies land, then provisions a non-bypass
``app_user`` role inside the container. Tests get two engines:

* ``pg_admin_engine`` -- BYPASSRLS, used by ``seed_*`` helpers to
  insert fixtures without tripping the policy. Mirrors what an ops
  migration job would do in production.
* ``pg_app_engine`` -- plain LOGIN role, subject to RLS. Used by the
  tests themselves to verify isolation.

Each test must open its own transaction and ``SET LOCAL`` the GUCs
before running queries; the GUCs are transaction-scoped so they don't
bleed between tests sharing the engine.
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


# pgvector image so the runtime-created ``canon_chunk_vectors`` test
# can also exercise the policy path even though the migration's DO
# block won't have fired (the table didn't exist when 0013 ran).
_PGVECTOR_IMAGE = "pgvector/pgvector:pg16"
_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()
_SKIP_REASON = "Docker + testcontainers required for RLS integration tests"


def _alembic_cfg(db_url: str) -> Config:
    """Build an Alembic config pointing at ``db_url``.

    The project's ``alembic.ini`` lives at the repo root; the env.py
    rewrites the ``+asyncpg`` driver suffix to ``+psycopg`` so we feed
    it the async-form URL directly.
    """
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _to_async_url(url: str) -> str:
    """Coerce the PostgresContainer URL into the asyncpg form alembic expects."""
    if url.startswith("postgresql+psycopg2"):
        return url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _to_sync_url(url: str) -> str:
    """Coerce a URL to the sync ``+psycopg`` form for direct SQLAlchemy use."""
    return url.replace("+asyncpg", "+psycopg")


def _swap_user(sync_url: str, username: str, password: str) -> str:
    """Return ``sync_url`` with the user/password swapped to ``app_user``.

    The PostgresContainer hands back a URL with the admin credentials
    baked in; we mint a parallel URL that talks to the same database
    but as the non-bypass role.
    """
    # postgresql+psycopg://test:test@host:port/test
    # -> postgresql+psycopg://app_user:app@host:port/test
    scheme_sep = "://"
    scheme, rest = sync_url.split(scheme_sep, 1)
    _, host_part = rest.split("@", 1)
    return f"{scheme}{scheme_sep}{username}:{password}@{host_part}"


@pytest.fixture(scope="module")
def pg_container() -> Iterator[PostgresContainer]:
    """Spin up the Postgres + pgvector container, run migrations, mint the app role."""
    if not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE):
        pytest.skip(_SKIP_REASON)
    with PostgresContainer(_PGVECTOR_IMAGE) as pg:
        admin_async = _to_async_url(pg.get_connection_url())
        # Run alembic to head -- 0013 lands the RLS policies.
        command.upgrade(_alembic_cfg(admin_async), "head")

        # Mint the non-bypass role. The default ``test`` superuser is
        # BYPASSRLS; we need a plain LOGIN role to actually exercise
        # the policies.
        admin_sync = _to_sync_url(admin_async)
        admin_engine = sa.create_engine(admin_sync, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            # Drop+recreate so re-runs (without rebuilding the
            # container) are deterministic. The CASCADE handles any
            # leftover ownership / grants.
            conn.execute(sa.text("DROP ROLE IF EXISTS app_user"))
            conn.execute(sa.text("CREATE ROLE app_user LOGIN PASSWORD 'app'"))
            conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO app_user"))
            conn.execute(sa.text("GRANT ALL ON ALL TABLES IN SCHEMA public TO app_user"))
            conn.execute(sa.text("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO app_user"))
            # Future tables (notably ``canon_chunk_vectors``, which is
            # runtime-created) need the same grant so the app role can
            # write to them.
            conn.execute(sa.text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app_user"))
            conn.execute(
                sa.text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO app_user")
            )
        admin_engine.dispose()

        yield pg


@pytest.fixture(scope="module")
def pg_admin_engine(pg_container: PostgresContainer) -> Iterator[sa.Engine]:
    """Sync engine connected as the admin (superuser, BYPASSRLS) role.

    Used by the ``seed_*`` helpers to insert fixtures without tripping
    the RLS policy -- the production analogue is the migration / ops
    role that owns the schema.
    """
    sync_url = _to_sync_url(_to_async_url(pg_container.get_connection_url()))
    engine = sa.create_engine(sync_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def pg_app_engine(pg_container: PostgresContainer) -> Iterator[sa.Engine]:
    """Sync engine connected as the non-bypass ``app_user`` role.

    All RLS-bound queries in the tests go through this engine; each
    test opens its own transaction (``with engine.begin() as conn``)
    and applies ``SET LOCAL app.tenant_id / app.workspace_id`` so the
    GUCs are scoped to that transaction and don't bleed between
    tests sharing the engine.
    """
    base = _to_sync_url(_to_async_url(pg_container.get_connection_url()))
    app_url = _swap_user(base, "app_user", "app")
    engine = sa.create_engine(app_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
