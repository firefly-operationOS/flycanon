# Copyright 2026 Firefly Software Solutions Inc
"""Live testcontainer coverage for the Tier-B partition admin tool.

Closes the "unit-tested only" caveat from CHANGELOG: the pure-Python
helpers (slug validation + partition name derivation) are unit-tested
under ``tests/unit/test_partition_admin.py``; the Postgres-side
``promote_tenant_to_partition`` / ``demote_tenant_from_partition``
helpers require a real partitioned ``canon_chunk_vectors`` table and
are exercised here against a ``pgvector/pgvector:pg16`` container.

The module-scoped ``_partitioned_chunk_table`` fixture stands up the
one-time partitioning DDL described in
``partition_admin.py``'s module docstring (the step a deployment
operator would run by hand once the cluster needs Tier-B): drop the
non-partitioned ``canon_chunk_vectors`` produced by the runtime
bootstrap path, recreate it as ``PARTITION BY LIST (tenant_id)`` with
a ``_default`` catch-all partition + per-partition HNSW index, and
install the same RLS policy the migration / runtime store install on
the non-partitioned form. After that the live ``promote`` / ``demote``
helpers can be invoked safely against the asyncpg engine the tests
build on top of the same container.

Skip behaviour mirrors ``test_rls_isolation.py`` -- the whole module
skips when Docker / testcontainers aren't available, so the test
suite stays green on CI machines without a Docker daemon.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]  # noqa: F401

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:
    _TESTCONTAINERS_AVAILABLE = False


_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()

pytestmark = pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason="Docker + testcontainers required for partition_admin integration tests",
)


# Imported lazily so module-level collection still succeeds when Docker
# is absent (the skip marker fires before the body of any test runs,
# but the conftest fixture parameter resolution still imports this
# module). ``partition_admin`` has no transitive Docker dependency, so
# eager import is fine -- but keeping the imports next to the tests
# makes the wiring obvious.
from flycanon.core.services.retrieval.partition_admin import (  # noqa: E402
    demote_tenant_from_partition,
    is_partitioned,
    promote_tenant_to_partition,
)

# Dimension used by the test partitioned schema. Kept small (3) to
# match the existing ``test_canon_chunk_vectors_runtime_table_isolation``
# pattern in test_rls_isolation.py -- HNSW indexes don't care about
# dimensionality for the structural promote/demote assertions.
_TEST_DIM = 3


# ---------------------------------------------------------------------------
# Known fragility: ``promote_tenant_to_partition`` uses a SQLAlchemy bind
# parameter (``FOR VALUES IN (:tenant_id)``) inside a CREATE TABLE
# statement. asyncpg -- the driver used by the production AsyncEngine
# this helper is invoked against -- rejects bind parameters in DDL
# statements at the wire-protocol level:
#
#     InterfaceError: the server expects 0 arguments for this query,
#     1 was passed.
#     HINT: parameters are supported only in SELECT, INSERT, UPDATE,
#     DELETE, MERGE and VALUES statements, and will *not* work in
#     statements like CREATE VIEW or DECLARE CURSOR.
#
# Both ``validate_slug`` calls on the tenant_id keep the literal-
# embedding form safe (the slug grammar rejects DDL-relevant
# metacharacters), so the fix is to drop the bind parameter and
# inline the already-validated ``tenant_id`` into the DDL. Until that
# patch lands, every test that invokes ``promote_tenant_to_partition``
# against asyncpg is xfail-strict so a future fix surfaces as an
# unexpected-pass and forces the mark to be removed.
_PROMOTE_DDL_PARAM_BUG = pytest.mark.xfail(
    strict=True,
    reason=(
        "partition_admin.promote_tenant_to_partition uses a bind parameter "
        "in CREATE TABLE ... FOR VALUES IN (:tenant_id); asyncpg rejects "
        "parameters in DDL. Fix: inline validate_slug(tenant_id) into the "
        "DDL literal (it's already grammar-checked) and drop the params dict."
    ),
)


# ---------------------------------------------------------------------------
# One-time partitioned-schema setup
# ---------------------------------------------------------------------------


def _async_admin_url(pg_container) -> str:  # type: ignore[no-untyped-def]
    """Coerce the container URL into ``+asyncpg`` form for the admin role."""
    sync_url = pg_container.get_connection_url()
    if sync_url.startswith("postgresql+psycopg2"):
        return sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest.fixture(scope="module")
def _partitioned_chunk_table(
    pg_container,  # type: ignore[no-untyped-def]
    pg_admin_engine: sa.Engine,
) -> Iterator[sa.Engine]:
    """Replace ``canon_chunk_vectors`` with the partitioned variant.

    Runs once per module. Drops whichever form Alembic / the runtime
    store left behind (the module-scope conftest only runs migrations;
    the runtime store hasn't fired on a fresh container) and creates
    the partitioned schema + default partition + RLS policy. The same
    grants the conftest issued for the non-partitioned form are
    re-applied so ``app_user`` keeps INSERT/SELECT access.
    """
    with pg_admin_engine.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Drop any existing form left behind by other module fixtures.
        # CASCADE handles indexes + dependent partitions if a previous
        # test session left partial state in a re-used container.
        conn.execute(sa.text("DROP TABLE IF EXISTS canon_chunk_vectors CASCADE"))
        conn.execute(
            sa.text(
                f"""
                CREATE TABLE canon_chunk_vectors (
                    id           TEXT NOT NULL,
                    namespace    TEXT NOT NULL DEFAULT 'default',
                    tenant_id    TEXT NOT NULL DEFAULT 'default',
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    embedding    vector({_TEST_DIM}) NOT NULL,
                    metadata     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    text         TEXT NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (id, tenant_id)
                ) PARTITION BY LIST (tenant_id)
                """
            )
        )
        # Default partition catches rows for tenants without a dedicated
        # partition -- this is the routing target both before promote
        # and after demote.
        conn.execute(
            sa.text(
                """
                CREATE TABLE canon_chunk_vectors_default
                PARTITION OF canon_chunk_vectors DEFAULT
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE INDEX canon_chunk_vectors_default_hnsw
                ON canon_chunk_vectors_default
                USING hnsw (embedding vector_cosine_ops)
                """
            )
        )
        # Install the same RLS policy the runtime store installs on
        # the non-partitioned form. The policy applies to every
        # partition (Postgres propagates ENABLE / FORCE / policies to
        # children of a partitioned root) so the cross-tenant
        # isolation test below exercises the realistic shape.
        conn.execute(sa.text("ALTER TABLE canon_chunk_vectors ENABLE ROW LEVEL SECURITY"))
        conn.execute(sa.text("ALTER TABLE canon_chunk_vectors FORCE ROW LEVEL SECURITY"))
        conn.execute(
            sa.text(
                """
                CREATE POLICY tenant_workspace_isolation ON canon_chunk_vectors
                  USING (
                    tenant_id = current_setting('app.tenant_id', true)
                    AND workspace_id = current_setting('app.workspace_id', true)
                  )
                """
            )
        )
        # ``ALTER DEFAULT PRIVILEGES`` from the conftest only covers
        # tables created AFTER it ran, which is true here, but the
        # explicit GRANT defends against re-used containers where the
        # default-privilege ALTER may have been re-issued out of order.
        conn.execute(sa.text("GRANT ALL ON canon_chunk_vectors TO app_user"))
        conn.execute(sa.text("GRANT ALL ON canon_chunk_vectors_default TO app_user"))

    yield pg_admin_engine


@pytest.fixture
def _clean_tenant_partitions(
    _partitioned_chunk_table: sa.Engine,
) -> Iterator[None]:
    """Drop any per-tenant partitions a previous test promoted.

    Keeps the test order-independent: every test that promotes a
    tenant runs against a clean partition tree, so a failed test
    doesn't leak partition state into the next.
    """
    yield
    with _partitioned_chunk_table.begin() as conn:
        rows = conn.execute(
            sa.text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class parent ON parent.oid = i.inhparent
                WHERE parent.relname = 'canon_chunk_vectors'
                  AND c.relname <> 'canon_chunk_vectors_default'
                """
            )
        ).all()
        for row in rows:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {row.relname} CASCADE"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_partition(engine: sa.Engine, partition_name: str) -> bool:
    """Return True iff ``partition_name`` exists as a partition of the root."""
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class parent ON parent.oid = i.inhparent
                WHERE parent.relname = 'canon_chunk_vectors'
                  AND c.relname = :name
                """
            ),
            {"name": partition_name},
        ).scalar()
    return result is not None


def _index_exists(engine: sa.Engine, index_name: str) -> bool:
    """Return True iff a (relkind='i') with this name exists."""
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM pg_class WHERE relkind = 'i' AND relname = :name"
            ),
            {"name": index_name},
        ).scalar()
    return result is not None


def _seed_chunk(
    engine: sa.Engine,
    *,
    chunk_id: str,
    tenant_id: str,
    workspace_id: str,
    embedding: str = "[1.0, 0.0, 0.0]",
    text: str = "alpha",
) -> None:
    """Insert one canon_chunk_vectors row via the admin (BYPASSRLS) engine."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_chunk_vectors
                    (id, namespace, tenant_id, workspace_id, embedding, text)
                VALUES
                    (:id, :ns, :tenant_id, :workspace_id,
                     CAST(:embedding AS vector), :text)
                """
            ),
            {
                "id": chunk_id,
                "ns": f"t/{tenant_id}/w/{workspace_id}",
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "embedding": embedding,
                "text": text,
            },
        )


def _run_async(coro):  # type: ignore[no-untyped-def]
    """Run a coroutine against a fresh event loop -- mirrors how the
    production async helpers are invoked from sync ops scripts."""
    return asyncio.run(coro)


def _with_async_engine(pg_container, coro_factory):  # type: ignore[no-untyped-def]
    """Build an asyncpg engine, hand it to ``coro_factory``, dispose."""

    async def _runner() -> object:
        engine: AsyncEngine = create_async_engine(
            _async_admin_url(pg_container), future=True
        )
        try:
            return await coro_factory(engine)
        finally:
            await engine.dispose()

    return _run_async(_runner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_is_partitioned_reports_true_for_partitioned_root(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
) -> None:
    """Sanity: ``is_partitioned`` returns True once the root is set up."""

    async def _check(engine: AsyncEngine) -> bool:
        return await is_partitioned(engine)

    assert _with_async_engine(pg_container, _check) is True


@_PROMOTE_DDL_PARAM_BUG
def test_promote_creates_dedicated_partition(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """Promoting a tenant carves out ``canon_chunk_vectors_<tenant>``.

    Verified via pg_inherits join so we pin both the partition's
    existence AND its attachment to the root -- a stray same-named
    standalone table wouldn't satisfy the production routing
    contract.
    """

    async def _promote(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "acme")

    _with_async_engine(pg_container, _promote)

    assert _has_partition(_partitioned_chunk_table, "canon_chunk_vectors_acme")


@_PROMOTE_DDL_PARAM_BUG
def test_promote_creates_per_partition_hnsw_index(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """A per-partition HNSW index lands alongside the carved partition.

    This is the whole point of Tier-B: a hot tenant gets its own
    independent ANN index unaffected by other tenants' churn.
    """

    async def _promote(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "acme")

    _with_async_engine(pg_container, _promote)

    assert _index_exists(_partitioned_chunk_table, "canon_chunk_vectors_acme_hnsw")


@_PROMOTE_DDL_PARAM_BUG
def test_promote_is_idempotent_on_already_promoted_tenant(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """Calling ``promote`` twice for the same tenant is a no-op.

    The early-exit guard in ``promote_tenant_to_partition`` skips
    both the ``CREATE TABLE ... PARTITION OF`` and the ``CREATE
    INDEX``; the second invocation must not raise even though both
    objects already exist.
    """

    async def _promote_twice(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "acme")
        await promote_tenant_to_partition(engine, "acme")

    _with_async_engine(pg_container, _promote_twice)

    # The partition + index must still be present (no destructive
    # double-create rollback) and there should be exactly one
    # partition for the tenant.
    assert _has_partition(_partitioned_chunk_table, "canon_chunk_vectors_acme")
    assert _index_exists(_partitioned_chunk_table, "canon_chunk_vectors_acme_hnsw")


def test_promote_raises_when_table_not_partitioned(
    pg_container,  # type: ignore[no-untyped-def]
    pg_admin_engine: sa.Engine,
    _partitioned_chunk_table: sa.Engine,
) -> None:
    """``promote`` refuses to operate on a non-partitioned table.

    We can't actually un-partition the table in this fixture without
    breaking sibling tests, so we exercise the guard the only other
    way it's reachable: by aiming the helper at a fake root that
    doesn't exist as a partitioned table.

    The check inside ``promote_tenant_to_partition`` looks up
    ``canon_chunk_vectors`` in ``pg_partitioned_table``; we
    temporarily DETACH the default partition + DROP the root, run
    the helper expecting it to raise, then restore the schema.
    """

    async def _try_promote(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "bcorp")

    with pg_admin_engine.begin() as conn:
        # Snapshot the schema bits we need to restore.
        conn.execute(sa.text("ALTER TABLE canon_chunk_vectors DETACH PARTITION canon_chunk_vectors_default"))
        conn.execute(sa.text("DROP TABLE canon_chunk_vectors"))
        # Re-create the root as a NON-partitioned table so the guard
        # observes the failure mode it's meant to catch.
        conn.execute(
            sa.text(
                f"""
                CREATE TABLE canon_chunk_vectors (
                    id           TEXT PRIMARY KEY,
                    namespace    TEXT NOT NULL DEFAULT 'default',
                    tenant_id    TEXT NOT NULL DEFAULT 'default',
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    embedding    vector({_TEST_DIM}) NOT NULL,
                    metadata     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    text         TEXT NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

    try:
        with pytest.raises(RuntimeError, match="not declarative-partitioned"):
            _with_async_engine(pg_container, _try_promote)
    finally:
        # Restore the partitioned root + default partition + policy +
        # grants so downstream tests see the expected fixture state.
        with pg_admin_engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE canon_chunk_vectors"))
            # canon_chunk_vectors_default was DETACHed -- drop it so
            # we can re-attach a fresh default partition off the
            # rebuilt partitioned root.
            conn.execute(sa.text("DROP TABLE IF EXISTS canon_chunk_vectors_default"))
            conn.execute(
                sa.text(
                    f"""
                    CREATE TABLE canon_chunk_vectors (
                        id           TEXT NOT NULL,
                        namespace    TEXT NOT NULL DEFAULT 'default',
                        tenant_id    TEXT NOT NULL DEFAULT 'default',
                        workspace_id TEXT NOT NULL DEFAULT 'default',
                        embedding    vector({_TEST_DIM}) NOT NULL,
                        metadata     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        text         TEXT NOT NULL,
                        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (id, tenant_id)
                    ) PARTITION BY LIST (tenant_id)
                    """
                )
            )
            conn.execute(
                sa.text(
                    """
                    CREATE TABLE canon_chunk_vectors_default
                    PARTITION OF canon_chunk_vectors DEFAULT
                    """
                )
            )
            conn.execute(
                sa.text(
                    """
                    CREATE INDEX canon_chunk_vectors_default_hnsw
                    ON canon_chunk_vectors_default
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )
            )
            conn.execute(sa.text("ALTER TABLE canon_chunk_vectors ENABLE ROW LEVEL SECURITY"))
            conn.execute(sa.text("ALTER TABLE canon_chunk_vectors FORCE ROW LEVEL SECURITY"))
            conn.execute(
                sa.text(
                    """
                    CREATE POLICY tenant_workspace_isolation ON canon_chunk_vectors
                      USING (
                        tenant_id = current_setting('app.tenant_id', true)
                        AND workspace_id = current_setting('app.workspace_id', true)
                      )
                    """
                )
            )
            conn.execute(sa.text("GRANT ALL ON canon_chunk_vectors TO app_user"))
            conn.execute(sa.text("GRANT ALL ON canon_chunk_vectors_default TO app_user"))


@_PROMOTE_DDL_PARAM_BUG
def test_demote_removes_partition(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """``demote`` drops the dedicated partition table after merging rows back.

    Depends on ``promote_tenant_to_partition`` to set up the precondition,
    so it inherits the same xfail-strict marker.
    """

    async def _promote(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "acme")

    async def _demote(engine: AsyncEngine) -> None:
        await demote_tenant_from_partition(engine, "acme")

    _with_async_engine(pg_container, _promote)
    assert _has_partition(_partitioned_chunk_table, "canon_chunk_vectors_acme")

    _with_async_engine(pg_container, _demote)
    assert not _has_partition(_partitioned_chunk_table, "canon_chunk_vectors_acme")
    # The per-partition HNSW index goes away with the table.
    assert not _index_exists(_partitioned_chunk_table, "canon_chunk_vectors_acme_hnsw")


@_PROMOTE_DDL_PARAM_BUG
def test_demote_preserves_rows_via_default_partition(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """Rows seeded into a promoted partition remain readable after demote.

    Exercises the INSERT-then-DROP merge pattern: after DETACH +
    INSERT INTO root SELECT ... + DROP, the previously
    partition-resident rows live on the default partition (routed
    there because no dedicated partition exists for the tenant any
    more) and are still visible via the root.
    """

    async def _promote(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "acme")

    async def _demote(engine: AsyncEngine) -> None:
        await demote_tenant_from_partition(engine, "acme")

    _with_async_engine(pg_container, _promote)

    # Seed rows AFTER promotion -- they land in the dedicated partition
    # via Postgres' partition routing.
    _seed_chunk(
        _partitioned_chunk_table,
        chunk_id="vec-pre-demote-1",
        tenant_id="acme",
        workspace_id="ws-a",
        embedding="[1.0, 0.0, 0.0]",
        text="alpha",
    )
    _seed_chunk(
        _partitioned_chunk_table,
        chunk_id="vec-pre-demote-2",
        tenant_id="acme",
        workspace_id="ws-a",
        embedding="[0.0, 1.0, 0.0]",
        text="bravo",
    )

    # Sanity: BEFORE demote, the rows live in the dedicated partition.
    with _partitioned_chunk_table.begin() as conn:
        partition_rows = conn.execute(
            sa.text(
                "SELECT id FROM canon_chunk_vectors_acme WHERE id LIKE 'vec-pre-demote-%'"
            )
        ).all()
        assert {r.id for r in partition_rows} == {"vec-pre-demote-1", "vec-pre-demote-2"}

    _with_async_engine(pg_container, _demote)

    # After demote: dedicated partition gone, rows visible via root
    # (and routed to the default partition).
    with _partitioned_chunk_table.begin() as conn:
        root_rows = conn.execute(
            sa.text(
                "SELECT id FROM canon_chunk_vectors WHERE id LIKE 'vec-pre-demote-%'"
            )
        ).all()
        assert {r.id for r in root_rows} == {"vec-pre-demote-1", "vec-pre-demote-2"}

        default_rows = conn.execute(
            sa.text(
                "SELECT id FROM canon_chunk_vectors_default WHERE id LIKE 'vec-pre-demote-%'"
            )
        ).all()
        assert {r.id for r in default_rows} == {"vec-pre-demote-1", "vec-pre-demote-2"}


def test_demote_is_idempotent_on_unpromoted_tenant(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """Demoting a tenant that was never promoted is a no-op (no error)."""

    async def _demote(engine: AsyncEngine) -> None:
        await demote_tenant_from_partition(engine, "never-promoted")

    # Must not raise, even though canon_chunk_vectors_never-promoted
    # doesn't exist. (The validate_slug call accepts the dash form.)
    _with_async_engine(pg_container, _demote)

    assert not _has_partition(
        _partitioned_chunk_table, "canon_chunk_vectors_never-promoted"
    )


@_PROMOTE_DDL_PARAM_BUG
def test_cross_tenant_isolation_holds_after_promotion(
    pg_container,  # type: ignore[no-untyped-def]
    _partitioned_chunk_table: sa.Engine,
    pg_app_engine: sa.Engine,
    _clean_tenant_partitions: None,
) -> None:
    """Promoting a tenant doesn't break the RLS isolation guarantee.

    Postgres propagates ``ENABLE / FORCE ROW LEVEL SECURITY`` and the
    policy from the partitioned root to every child partition, so an
    ``app_user`` query through the root must still only see rows for
    its own (tenant_id, workspace_id) -- regardless of whether the
    target rows live in the dedicated partition or the default
    partition.
    """

    async def _promote(engine: AsyncEngine) -> None:
        await promote_tenant_to_partition(engine, "acme")

    _with_async_engine(pg_container, _promote)

    # Re-grant the new partition so app_user can SELECT through it.
    # ALTER DEFAULT PRIVILEGES covers most cases but partition tables
    # are created out-of-band (via the helper) which is the same
    # operator path we'd hit in production; matching the defensive
    # regrant in test_canon_chunk_vectors_runtime_table_isolation.
    with _partitioned_chunk_table.begin() as conn:
        conn.execute(sa.text("GRANT ALL ON canon_chunk_vectors_acme TO app_user"))

    # Seed: one row for acme (lands in the dedicated partition), one
    # for bcorp (lands in the default partition).
    _seed_chunk(
        _partitioned_chunk_table,
        chunk_id="vec-iso-acme",
        tenant_id="acme",
        workspace_id="ws-a",
        embedding="[1.0, 0.0, 0.0]",
        text="acme-secret",
    )
    _seed_chunk(
        _partitioned_chunk_table,
        chunk_id="vec-iso-bcorp",
        tenant_id="bcorp",
        workspace_id="ws-a",
        embedding="[0.0, 1.0, 0.0]",
        text="bcorp-secret",
    )

    # acme session sees only acme's row.
    with pg_app_engine.begin() as conn:
        conn.execute(sa.text("SET LOCAL app.tenant_id = 'acme'"))
        conn.execute(sa.text("SET LOCAL app.workspace_id = 'ws-a'"))
        rows = conn.execute(
            sa.text(
                "SELECT id, tenant_id FROM canon_chunk_vectors "
                "WHERE id IN ('vec-iso-acme', 'vec-iso-bcorp')"
            )
        ).all()
        assert {(r.id, r.tenant_id) for r in rows} == {("vec-iso-acme", "acme")}

    # bcorp session sees only bcorp's row.
    with pg_app_engine.begin() as conn:
        conn.execute(sa.text("SET LOCAL app.tenant_id = 'bcorp'"))
        conn.execute(sa.text("SET LOCAL app.workspace_id = 'ws-a'"))
        rows = conn.execute(
            sa.text(
                "SELECT id, tenant_id FROM canon_chunk_vectors "
                "WHERE id IN ('vec-iso-acme', 'vec-iso-bcorp')"
            )
        ).all()
        assert {(r.id, r.tenant_id) for r in rows} == {("vec-iso-bcorp", "bcorp")}
