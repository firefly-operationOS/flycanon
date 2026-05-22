# Copyright 2026 Firefly Software Solutions Inc
"""Shared async-engine helpers for the repository layer.

Every repository runs against the same async engine; building one per
repository would multiply Postgres connection pools needlessly. The
:func:`build_engine` factory hands out a cached singleton keyed on
the database URL.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from flycanon.web.conventions.db import install_tenant_guc_hook

_engine_cache: dict[tuple[str, bool], AsyncEngine] = {}


def build_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Return the cached ``AsyncEngine`` for ``database_url``.

    Repositories may be instantiated more than once during a process
    lifetime (tests, the worker reusing the API's container);
    sharing the engine keeps the connection-pool footprint small.

    The first call installs the per-transaction RLS GUC listener
    (``after_begin`` on :class:`sqlalchemy.orm.Session`). The listener
    is idempotent so subsequent calls -- including the worker's own
    engine boot, or a test suite that constructs several engines --
    re-use the same registration.
    """
    # Install the RLS GUC listener on first engine creation. The hook
    # is keyed on a sentinel inside ``install_tenant_guc_hook`` so
    # calling it on every ``build_engine`` is cheap and safe (the
    # listener registers exactly once).
    install_tenant_guc_hook()

    key = (database_url, echo)
    cached = _engine_cache.get(key)
    if cached is None:
        cached = create_async_engine(
            database_url,
            echo=echo,
            future=True,
            pool_pre_ping=True,
        )
        _engine_cache[key] = cached
    return cached


def build_session_factory(
    database_url: str, *, echo: bool = False
) -> tuple[
    async_sessionmaker[AsyncSession],
    AsyncEngine,
]:
    engine = build_engine(database_url, echo=echo)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine
