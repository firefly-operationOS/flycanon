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

"""End-to-end: real Postgres + RLS + middleware + controller pipeline.

This is the CRITICAL test that pins production behavior under a
non-``BYPASSRLS`` role. The existing ``test_rls_isolation.py`` suite
uses raw SQL ``SET LOCAL`` to apply the GUCs directly, which proves
the migration's policies work but doesn't exercise the application
pipeline (middleware -> after_begin hook -> GUCs -> RLS). Without
the :class:`TenantContextMiddleware`, that pipeline silently breaks:

1. Pyfly's ``@rest_controller`` parameter resolver bypasses FastAPI
   ``Depends``, so :func:`require_tenant_context` -- the canonical
   ContextVar-binding dependency -- never fires for pyfly routes.
2. Controllers call :func:`tenant_context_from_request` instead,
   which builds a :class:`TenantContext` but does NOT bind the
   ContextVar.
3. When the controller hits a repository, the repository opens an
   :class:`AsyncSession`; SQLAlchemy fires ``after_begin``.
4. :func:`install_tenant_guc_hook`'s listener reads
   :func:`current_tenant_context` -> ``None`` -> SKIPS the
   ``SET LOCAL app.tenant_id`` / ``app.workspace_id`` writes.
5. Postgres ``current_setting('app.tenant_id', true)`` returns the
   empty string; the RLS policy comparison fails; every read returns
   zero rows.

In dev/test the connection role is the admin superuser
(``BYPASSRLS``), which silently masks the bug. This test runs as the
non-bypass ``app_user`` role so the middleware's binding is the only
thing keeping reads alive.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]  # noqa: F401

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:
    _TESTCONTAINERS_AVAILABLE = False


_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()

pytestmark = pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason="Docker + testcontainers required for RLS integration tests",
)


def _seed_workspace(
    engine: sa.Engine,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
) -> None:
    """Seed a workspace row via the admin (BYPASSRLS) engine."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_workspaces (id, tenant_id, name, status)
                VALUES (:id, :tenant_id, :name, 'active')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": workspace_id, "tenant_id": tenant_id, "name": name},
        )


def _app_async_url(pg_container) -> str:  # type: ignore[no-untyped-def]
    """Build the ``+asyncpg`` URL talking as the non-bypass app_user role."""
    sync_url = pg_container.get_connection_url()
    if sync_url.startswith("postgresql+psycopg2"):
        async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    elif sync_url.startswith("postgresql://"):
        async_url = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        async_url = sync_url
    # Swap the admin user/password for the non-bypass app_user/app pair.
    scheme, rest = async_url.split("://", 1)
    _, host_part = rest.split("@", 1)
    return f"{scheme}://app_user:app@{host_part}"


def _make_request(headers: dict[str, str]) -> Request:
    """Build a real Starlette :class:`Request` carrying ``headers``."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw,
    }
    return Request(scope)


async def _drive_pipeline(
    *,
    app_async_url: str,
    headers: dict[str, str],
    operation: str,
    tenant_id: str,
    workspace_id: str,
) -> dict[str, object]:
    """Run a single request through middleware -> repository -> RLS.

    The middleware binds the ContextVar from headers; the repository
    opens an :class:`AsyncSession`; the ``after_begin`` listener
    issues the ``SET LOCAL`` GUCs; the RLS policy then filters the
    query. We assert what the query saw.
    """
    from flycanon.models.repositories.workspace_repository import WorkspaceRepository
    from flycanon.web.conventions.db import install_tenant_guc_hook
    from flycanon.web.conventions.middleware import TenantContextMiddleware

    # Install the listener (idempotent) -- the production path does
    # this inside ``build_engine``; we drive a custom engine here so
    # we call it explicitly.
    install_tenant_guc_hook()

    engine = create_async_engine(app_async_url, future=True, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = WorkspaceRepository(factory, engine=engine)

    captured: dict[str, object] = {}

    async def _route(_request: Request) -> JSONResponse:
        # Mirrors what a real pyfly controller does: open a session,
        # query a repository. The middleware should have bound the
        # ContextVar already, so the after_begin listener will issue
        # the SET LOCAL GUCs on the session's first transaction.
        if operation == "get":
            row = await repo.get(tenant_id=tenant_id, workspace_id=workspace_id)
            captured["row"] = row
        elif operation == "list":
            rows = await repo.list_for_tenant(tenant_id)
            captured["rows"] = rows
        else:  # pragma: no cover -- guard against typos
            raise RuntimeError(f"unknown operation {operation!r}")
        return JSONResponse({"ok": True})

    middleware = TenantContextMiddleware(app=lambda *_: None)  # type: ignore[arg-type]
    request = _make_request(headers)
    try:
        await middleware.dispatch(request, _route)
    finally:
        await engine.dispose()
    return captured


# ---------------------------------------------------------------------------
# The fix's primary contract: a request with valid headers should be
# able to read the seeded row through the controller pipeline even
# under a non-BYPASSRLS role. Without the middleware, this returns
# None and the test fails.
# ---------------------------------------------------------------------------


def test_middleware_binds_ctx_so_rls_allows_read(
    pg_container,  # type: PostgresContainer
    pg_admin_engine: sa.Engine,
) -> None:
    """Seeded row is visible through the controller pipeline as app_user.

    Without :class:`TenantContextMiddleware`, the after_begin hook reads
    ``current_tenant_context() is None`` and skips SET LOCAL; the RLS
    policy then filters the row out and the repository returns None.
    """
    workspace_id = f"ws-mw-{uuid.uuid4().hex[:8]}"
    _seed_workspace(
        pg_admin_engine,
        tenant_id="acme",
        workspace_id=workspace_id,
        name="middleware-read",
    )

    app_async_url = _app_async_url(pg_container)
    result = asyncio.run(
        _drive_pipeline(
            app_async_url=app_async_url,
            headers={
                "X-Tenant-Id": "acme",
                "X-Workspace-Id": workspace_id,
            },
            operation="get",
            tenant_id="acme",
            workspace_id=workspace_id,
        )
    )

    row = result.get("row")
    assert row is not None, (
        "Middleware did not bind the ContextVar -- after_begin skipped SET LOCAL -- RLS filtered the row out"
    )
    assert isinstance(row, dict)
    assert row["id"] == workspace_id
    assert row["tenant_id"] == "acme"
    assert row["name"] == "middleware-read"


# ---------------------------------------------------------------------------
# Cross-workspace read isolation through the controller pipeline. Two
# rows under the same tenant; the request scope only sees its own.
# ---------------------------------------------------------------------------


def test_middleware_blocks_cross_workspace_read(
    pg_container,  # type: PostgresContainer
    pg_admin_engine: sa.Engine,
) -> None:
    """A request scoped to ws-A must not see ws-B's row.

    Even though both rows belong to the same tenant, the
    workspace-scoped RLS policy (``USING tenant_id = current_setting +
    id = current_setting``) filters every other workspace out.
    """
    ws_a = f"ws-iso-a-{uuid.uuid4().hex[:8]}"
    ws_b = f"ws-iso-b-{uuid.uuid4().hex[:8]}"
    _seed_workspace(pg_admin_engine, tenant_id="acme", workspace_id=ws_a, name="alpha")
    _seed_workspace(pg_admin_engine, tenant_id="acme", workspace_id=ws_b, name="bravo")

    app_async_url = _app_async_url(pg_container)
    # Request scope is ws-A -- ask for ws-B's row.
    result = asyncio.run(
        _drive_pipeline(
            app_async_url=app_async_url,
            headers={
                "X-Tenant-Id": "acme",
                "X-Workspace-Id": ws_a,
            },
            operation="get",
            tenant_id="acme",
            workspace_id=ws_b,  # asking for the OTHER workspace
        )
    )
    assert result.get("row") is None, (
        "Cross-workspace read should be blocked by RLS, but the foreign workspace row leaked through"
    )

    # And the request can see its own row when asking for it.
    result_self = asyncio.run(
        _drive_pipeline(
            app_async_url=app_async_url,
            headers={
                "X-Tenant-Id": "acme",
                "X-Workspace-Id": ws_a,
            },
            operation="get",
            tenant_id="acme",
            workspace_id=ws_a,
        )
    )
    self_row = result_self.get("row")
    assert self_row is not None
    assert isinstance(self_row, dict)
    assert self_row["id"] == ws_a


# ---------------------------------------------------------------------------
# Without the middleware, the same pipeline returns None for the row.
# This is the regression guard: if a future refactor removes the
# middleware (or its ordering breaks), this test catches it. We drive
# the pipeline directly without the middleware to demonstrate the
# masked failure mode.
# ---------------------------------------------------------------------------


def test_pipeline_without_middleware_silently_returns_none(
    pg_container,  # type: PostgresContainer
    pg_admin_engine: sa.Engine,
) -> None:
    """Pipeline WITHOUT the middleware -> RLS hides the row.

    This is the bug the middleware fixes; pinning it here means
    a future regression that drops the middleware (or wires it after
    the route) gets caught by a failing assertion instead of by an
    invisible empty-result production outage.
    """
    from flycanon.models.repositories.workspace_repository import WorkspaceRepository
    from flycanon.web.conventions.db import install_tenant_guc_hook

    install_tenant_guc_hook()

    workspace_id = f"ws-bug-{uuid.uuid4().hex[:8]}"
    _seed_workspace(
        pg_admin_engine,
        tenant_id="acme",
        workspace_id=workspace_id,
        name="exposed-without-middleware",
    )

    async def _run() -> dict[str, object] | None:
        engine = create_async_engine(_app_async_url(pg_container), future=True, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = WorkspaceRepository(factory, engine=engine)
        try:
            # No middleware -> no ContextVar binding -> after_begin
            # skips SET LOCAL -> RLS sees an unset GUC -> zero rows.
            return await repo.get(tenant_id="acme", workspace_id=workspace_id)
        finally:
            await engine.dispose()

    row = asyncio.run(_run())
    assert row is None, (
        "Repository should return None when the middleware is bypassed -- "
        "this is the bug the TenantContextMiddleware fixes. If this row is "
        "now visible without middleware, either the ContextVar leaked from "
        "another test or install_tenant_guc_hook has a regression."
    )


# ---------------------------------------------------------------------------
# Exception in the route still resets the ContextVar -- after the
# request, current_tenant_context() must be None so the next request
# does not inherit the previous tenant scope.
# ---------------------------------------------------------------------------


def test_middleware_resets_contextvar_after_route_exception(
    pg_container,  # type: PostgresContainer
) -> None:
    """Route raises -> ContextVar reset -> a follow-up bare query sees nothing.

    Production scenario: a route raises after the middleware binds.
    The middleware's ``finally`` must reset the token so the next
    request on the same event-loop task does not inherit ``acme``'s
    scope. We assert this end-to-end by running a follow-up bare
    repository query (no middleware) and confirming it returns None
    -- which it would NOT do if the previous request's ContextVar
    still held ``acme/ws-X``.
    """
    from flycanon.models.repositories.workspace_repository import WorkspaceRepository
    from flycanon.web.conventions.context import current_tenant_context
    from flycanon.web.conventions.db import install_tenant_guc_hook
    from flycanon.web.conventions.middleware import TenantContextMiddleware

    install_tenant_guc_hook()

    app_async_url = _app_async_url(pg_container)

    class _BoomError(RuntimeError):
        pass

    async def _route(_req: Request) -> JSONResponse:
        # Sanity: the middleware bound the ctx before we got here.
        assert current_tenant_context() is not None
        raise _BoomError("route exploded")

    async def _run() -> None:
        middleware = TenantContextMiddleware(app=lambda *_: None)  # type: ignore[arg-type]
        request = _make_request({"X-Tenant-Id": "acme", "X-Workspace-Id": "ws-boom"})
        # Route raises -- middleware must still reset the ContextVar.
        with contextlib.suppress(_BoomError):
            await middleware.dispatch(request, _route)
        # After the dispatch, the ContextVar is reset to None.
        assert current_tenant_context() is None

        # And the next bare repo query (no middleware, no ctx) sees
        # zero rows -- which confirms the prior scope did not leak.
        engine = create_async_engine(app_async_url, future=True, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = WorkspaceRepository(factory, engine=engine)
        try:
            row = await repo.get(tenant_id="acme", workspace_id="ws-boom")
            assert row is None
        finally:
            await engine.dispose()

    asyncio.run(_run())
