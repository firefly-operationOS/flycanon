# Copyright 2026 Firefly Software Solutions Inc
"""``set_tenant_guc`` -- helper that sets the per-transaction GUCs.

SQLite is the only dialect the unit suite has access to. Postgres
GUCs are a no-op there, so the helper must exit cleanly without
issuing ``SET LOCAL`` (which SQLite would reject). The real Postgres
path is exercised by the testcontainer-backed integration test in
``tests/integration/test_rls_isolation.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from flycanon.web.conventions.context import TenantContext
from flycanon.web.conventions.db import set_tenant_guc


@pytest.fixture
def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="acme",
        workspace_id="ws-q3",
        actor=None,
        correlation_id="01J5XYZ",
    )


async def test_set_tenant_guc_is_noop_on_sqlite(_ctx: TenantContext) -> None:
    """SQLite has no GUCs -- the helper must skip without raising."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with AsyncSession(engine) as session:
            # On SQLite this is a no-op -- no SET LOCAL is issued, no
            # exception leaks out. Calling it inside an open session
            # still has to be safe because the production hook fires
            # at session-open time regardless of the underlying dialect.
            await set_tenant_guc(session, _ctx)
    finally:
        await engine.dispose()


async def test_set_tenant_guc_is_noop_when_bind_is_sqlite_inside_txn(
    _ctx: TenantContext,
) -> None:
    """Same no-op contract when a transaction is already in progress."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with AsyncSession(engine) as session, session.begin():
            await set_tenant_guc(session, _ctx)
    finally:
        await engine.dispose()
