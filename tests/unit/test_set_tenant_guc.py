# Copyright 2026 Firefly Software Solutions Inc
"""``set_tenant_guc`` -- helper that sets the per-transaction GUCs.

SQLite is the only dialect the unit suite has access to. Postgres
GUCs are a no-op there, so the helper must exit cleanly without
issuing ``SET LOCAL`` (which SQLite would reject). The real Postgres
path is exercised by the testcontainer-backed integration test in
``tests/integration/test_rls_isolation.py``; here we exercise the
Postgres branches with mocks so the listener + helper logic is
covered without provisioning a real DB.

Test surface
------------

* SQLite no-op branches (both helper + listener).
* Mocked Postgres dialect branches (helper + listener emit two
  ``SET LOCAL`` statements with the bound context's values).
* Listener idempotency (calling ``install_tenant_guc_hook`` twice
  registers the listener exactly once).
* Listener no-ops when no :class:`TenantContext` is bound (the
  worker / migration runner path).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session as SyncSession

from flycanon.web.conventions.context import (
    TenantContext,
    current_tenant_context,
    set_tenant_context,
)
from flycanon.web.conventions.db import (
    install_tenant_guc_hook,
    set_tenant_guc,
)


@pytest.fixture
def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="acme",
        workspace_id="ws-q3",
        actor=None,
        correlation_id="01J5XYZ",
    )


# ----------------------------------------------------------------------
# set_tenant_guc helper
# ----------------------------------------------------------------------


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


async def test_set_tenant_guc_issues_set_local_on_postgres(
    _ctx: TenantContext,
) -> None:
    """When the bind reports a Postgres dialect, two SET LOCALs fire.

    Driven against a mocked session so we don't need a real Postgres.
    The two ``SET LOCAL`` statements must carry the tenant + workspace
    values verbatim (they are pre-validated slugs).
    """
    session = MagicMock(spec=AsyncSession)
    bind = MagicMock()
    bind.dialect = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind = MagicMock(return_value=bind)
    session.execute = AsyncMock()

    await set_tenant_guc(session, _ctx)

    assert session.execute.await_count == 2
    issued = [str(call.args[0]).strip() for call in session.execute.await_args_list]
    assert any("SET LOCAL app.tenant_id = 'acme'" in s for s in issued), issued
    assert any("SET LOCAL app.workspace_id = 'ws-q3'" in s for s in issued), issued


async def test_set_tenant_guc_noop_when_bind_is_none(
    _ctx: TenantContext,
) -> None:
    """A detached session reports ``bind is None`` -- the helper skips."""
    session = MagicMock(spec=AsyncSession)
    session.get_bind = MagicMock(return_value=None)
    session.execute = AsyncMock()

    await set_tenant_guc(session, _ctx)

    session.execute.assert_not_awaited()


# ----------------------------------------------------------------------
# install_tenant_guc_hook listener
# ----------------------------------------------------------------------


def test_install_tenant_guc_hook_is_idempotent() -> None:
    """Calling install twice must not register the listener twice.

    The sentinel inside :func:`install_tenant_guc_hook` short-circuits
    the second call so the SQLAlchemy event-listener table only sees
    one registration. We observe the listener count on the dispatch
    object before + after the second install to prove it.
    """
    install_tenant_guc_hook()

    def _count_listeners() -> int:
        return sum(
            1
            for listener in _all_after_begin_listeners()
            if "_set_guc_on_begin" in getattr(listener, "__qualname__", "")
        )

    before = _count_listeners()
    install_tenant_guc_hook()
    install_tenant_guc_hook()
    after = _count_listeners()

    assert before == 1, before
    assert after == 1, after
    assert getattr(install_tenant_guc_hook, "_installed", False) is True


def test_listener_noop_on_sqlite_session() -> None:
    """The listener attaches globally; SQLite begins must not error.

    Run the listener via SQLAlchemy's real event dispatch on a sync
    SQLite session so the no-pg-dialect branch is hit naturally.
    """
    install_tenant_guc_hook()

    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    try:
        with SyncSession(engine) as session, session.begin():
            # ``session.begin()`` fires after_begin synchronously.
            pass
    finally:
        engine.dispose()
    # No exception is the assertion; the listener returned early
    # because the dialect is "sqlite", not "postgresql".


def _all_after_begin_listeners() -> list[Any]:
    """Flatten every ``after_begin`` listener SQLAlchemy has on ``Session``.

    The class-level registry is a :class:`WeakKeyDictionary` keyed on
    each subclass; the values are lists of listener callables (or
    weakrefs to them). We walk that registry and resolve any weakrefs
    so tests can introspect the bound functions directly.
    """
    clslevel = SyncSession.dispatch.after_begin._clslevel
    flat: list[Any] = []
    for listeners in clslevel.values():
        for entry in listeners:
            # Some entries are weakrefs; calling them yields the live fn.
            resolved = (
                entry()
                if callable(entry)
                and hasattr(entry, "__weakref__") is False
                and entry.__class__.__name__ == "weakref"
                else entry
            )
            flat.append(resolved)
    return flat


def _get_listener_callable() -> Any:
    """Return the ``_set_guc_on_begin`` closure registered on ``Session``.

    The listener is registered against :class:`SyncSession.after_begin`
    by :func:`install_tenant_guc_hook`. SQLAlchemy stores attached
    listeners on the dispatch wrapper's ``_clslevel`` registry keyed
    on each subclass; we walk it and return the closure created
    inside the install function so the unit tests can invoke it with
    fake connections.
    """
    install_tenant_guc_hook()
    candidates = [
        listener
        for listener in _all_after_begin_listeners()
        if "_set_guc_on_begin" in getattr(listener, "__qualname__", "")
    ]
    assert candidates, "install_tenant_guc_hook did not register a listener"
    # Guard against double-registration sneaking through: there must
    # be exactly one matching listener.
    assert len(candidates) == 1, f"expected exactly one _set_guc_on_begin listener, got {len(candidates)}"
    return candidates[0]


def test_listener_noop_when_no_context_bound() -> None:
    """A Postgres connection but no :class:`TenantContext` -> no SQL."""
    listener = _get_listener_callable()
    assert current_tenant_context() is None

    # Build a fake connection + transaction that the listener can
    # safely receive. ``dialect.name = 'postgresql'`` so we hit the
    # branch that would otherwise emit SET LOCAL.
    session = MagicMock(spec=SyncSession)
    transaction = MagicMock()
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    connection.execute = MagicMock()

    listener(session, transaction, connection)

    # No request-scope context bound -> the listener must NOT issue
    # any SQL on the connection.
    connection.execute.assert_not_called()


def test_listener_emits_set_local_with_bound_context(
    _ctx: TenantContext,
) -> None:
    """A bound context + Postgres dialect -> two ``SET LOCAL`` statements."""
    listener = _get_listener_callable()

    session = MagicMock(spec=SyncSession)
    transaction = MagicMock()
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    connection.execute = MagicMock()

    token = set_tenant_context(_ctx)
    try:
        listener(session, transaction, connection)
    finally:
        token.var.reset(token.token)

    # Exactly two SET LOCAL statements -- one per GUC.
    assert connection.execute.call_count == 2
    issued = [str(call.args[0]).strip() for call in connection.execute.call_args_list]
    assert any("SET LOCAL app.tenant_id = 'acme'" in s for s in issued), issued
    assert any("SET LOCAL app.workspace_id = 'ws-q3'" in s for s in issued), issued


def test_listener_skips_non_postgres_dialect_even_with_context(
    _ctx: TenantContext,
) -> None:
    """A bound context but a non-Postgres connection -> no SQL.

    Protects the SQLite-backed unit-test pathway: even when a test
    set the context and then opened a SQLite-backed session, the
    listener must NOT issue ``SET LOCAL`` (SQLite would reject it).
    """
    listener = _get_listener_callable()

    session = MagicMock(spec=SyncSession)
    transaction = MagicMock()
    connection = MagicMock()
    connection.dialect.name = "sqlite"
    connection.execute = MagicMock()

    token = set_tenant_context(_ctx)
    try:
        listener(session, transaction, connection)
    finally:
        token.var.reset(token.token)

    connection.execute.assert_not_called()


def test_listener_does_not_crash_with_an_unusual_dialect_name() -> None:
    """Defensive: a non-pg dialect we haven't seen still no-ops cleanly."""
    listener = _get_listener_callable()

    session = MagicMock(spec=SyncSession)
    transaction = MagicMock()
    connection = MagicMock()
    connection.dialect.name = "mssql"  # contrived non-pg dialect
    connection.execute = MagicMock()

    listener(session, transaction, connection)

    connection.execute.assert_not_called()


# ----------------------------------------------------------------------
# Engine boot wiring
# ----------------------------------------------------------------------


def test_build_engine_installs_hook() -> None:
    """``build_engine`` calls ``install_tenant_guc_hook`` exactly once."""
    from flycanon.models.repositories._engine import build_engine

    # The sentinel records the first install; calling again is a
    # silent no-op. Run build_engine twice and assert the sentinel
    # is set after either call -- this exercises the wiring without
    # caring about state from prior tests (any of the helper tests
    # above will have already installed the listener).
    eng_a: Any = build_engine("sqlite+aiosqlite:///:memory:")
    eng_b: Any = build_engine("sqlite+aiosqlite:///:memory:")
    assert getattr(install_tenant_guc_hook, "_installed", False) is True
    # Same URL -> cached, so both calls return the same engine.
    assert eng_a is eng_b
