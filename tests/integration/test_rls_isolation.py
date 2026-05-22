# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end verification of migration ``0013_rls_policies``.

Boots a real Postgres + pgvector container, runs ``alembic upgrade
head`` so the RLS policies land, then provisions a non-bypass
``app_user`` role and exercises the policies the way the application
exercises them: ``SET LOCAL app.tenant_id`` / ``app.workspace_id`` at
transaction start, query, assert isolation.

The migration writes ``USING``-only policies. Postgres auto-derives
the ``WITH CHECK`` clause from the ``USING`` expression when it's
omitted (see https://www.postgresql.org/docs/current/sql-createpolicy.html),
so the policy defends BOTH the read path (USING) and the write path
(implicit WITH CHECK = USING). That gives us the stronger guarantee:
``app_user`` cannot smuggle a row into another tenant's / workspace's
scope even if the application code had a bug -- the write itself
fails with ``new row violates row-level security policy``. The
``test_insert_with_mismatched_scope_is_rejected`` case pins this
behaviour so a future migration adding an explicit (and possibly
weaker) ``WITH CHECK`` doesn't silently regress.

Skip behaviour: the suite auto-skips when Docker / testcontainers
aren't available, matching the pattern in
``tests/unit/test_pgvector_scope_isolation.py``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

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


# ---------------------------------------------------------------------------
# Seed helpers -- always go through ``pg_admin_engine`` so the inserts
# aren't subject to RLS. The seeded rows simulate the per-scope state
# that an ops migration job (the BYPASSRLS path) would put in place.
# ---------------------------------------------------------------------------


def _seed_workspace(
    engine: sa.Engine,
    *,
    workspace_id: str,
    tenant_id: str,
    name: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_workspaces (id, tenant_id, name, status)
                VALUES (:id, :tenant_id, :name, 'active')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": workspace_id,
                "tenant_id": tenant_id,
                "name": name or f"{tenant_id}/{workspace_id}",
            },
        )


def _seed_knowledge_item(
    engine: sa.Engine,
    *,
    item_id: str,
    tenant_id: str,
    workspace_id: str,
    title: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_knowledge_items
                    (id, tenant_id, workspace_id, status, current_version,
                     title, domain, jurisdiction, tags_json, metadata_json)
                VALUES
                    (:id, :tenant_id, :workspace_id, 'active', 1,
                     :title, 'general', 'GLOBAL', '[]'::jsonb, '{}'::jsonb)
                """
            ),
            {
                "id": item_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "title": title,
            },
        )


def _seed_agent_token(
    engine: sa.Engine,
    *,
    token_id: str,
    tenant_id: str,
    prefix: str,
    workspace_allowlist: list[str] | None = None,
) -> None:
    """Insert a token row -- tenant-scoped, no workspace_id column."""
    import json

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_agent_tokens
                    (id, tenant_id, name, prefix, secret_hash,
                     workspace_allowlist_json, scopes_json, created_by)
                VALUES
                    (:id, :tenant_id, :name, :prefix, :secret_hash,
                     CAST(:allowlist AS JSON), CAST(:scopes AS JSON), 'test')
                """
            ),
            {
                "id": token_id,
                "tenant_id": tenant_id,
                "name": f"token-{prefix}",
                "prefix": prefix,
                "secret_hash": "x" * 64,
                "allowlist": json.dumps(workspace_allowlist) if workspace_allowlist else None,
                "scopes": json.dumps(["agent.search"]),
            },
        )


def _set_scope(conn: sa.Connection, *, tenant_id: str, workspace_id: str) -> None:
    """Apply the GUCs the application's ``after_begin`` hook would set.

    ``SET LOCAL`` is transaction-scoped; this MUST be called inside an
    open transaction (``with engine.begin() as conn``) so the values
    roll off at commit/rollback and don't leak into sibling tests.
    """
    conn.execute(sa.text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
    conn.execute(sa.text(f"SET LOCAL app.workspace_id = '{workspace_id}'"))


# ---------------------------------------------------------------------------
# Cross-workspace isolation (same tenant)
# ---------------------------------------------------------------------------


def test_workspace_isolation_same_tenant(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Same tenant, two workspaces -> each session sees only its own rows."""
    item_a = str(uuid.uuid4())
    item_b = str(uuid.uuid4())
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_a,
        tenant_id="acme",
        workspace_id="ws-a",
        title="alpha",
    )
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_b,
        tenant_id="acme",
        workspace_id="ws-b",
        title="bravo",
    )

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-a")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id IN (:a, :b)"),
            {"a": item_a, "b": item_b},
        ).all()
        assert {r.id for r in rows} == {item_a}

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-b")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id IN (:a, :b)"),
            {"a": item_a, "b": item_b},
        ).all()
        assert {r.id for r in rows} == {item_b}


# ---------------------------------------------------------------------------
# Cross-tenant isolation (same workspace_id slug)
# ---------------------------------------------------------------------------


def test_tenant_isolation_same_workspace_slug(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Different tenants colliding on a workspace_id slug -> no leak."""
    item_acme = str(uuid.uuid4())
    item_bcorp = str(uuid.uuid4())
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_acme,
        tenant_id="acme",
        workspace_id="shared-slug",
        title="acme-secret",
    )
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_bcorp,
        tenant_id="bcorp",
        workspace_id="shared-slug",
        title="bcorp-secret",
    )

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="shared-slug")
        rows = conn.execute(
            sa.text("SELECT id, tenant_id FROM canon_knowledge_items WHERE id IN (:a, :b)"),
            {"a": item_acme, "b": item_bcorp},
        ).all()
        assert {(r.id, r.tenant_id) for r in rows} == {(item_acme, "acme")}

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="bcorp", workspace_id="shared-slug")
        rows = conn.execute(
            sa.text("SELECT id, tenant_id FROM canon_knowledge_items WHERE id IN (:a, :b)"),
            {"a": item_acme, "b": item_bcorp},
        ).all()
        assert {(r.id, r.tenant_id) for r in rows} == {(item_bcorp, "bcorp")}


# ---------------------------------------------------------------------------
# Unset-GUC failure mode -- ``current_setting('app.tenant_id', true)``
# returns NULL when never set, the policy comparison fails, and the
# query sees zero rows. This is the "fail closed" property of the
# whole scheme.
# ---------------------------------------------------------------------------


def test_unset_gucs_yield_zero_rows(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Without GUCs the policy can't match -> zero rows visible."""
    item_id = str(uuid.uuid4())
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_id,
        tenant_id="acme",
        workspace_id="ws-a",
        title="invisible",
    )

    with pg_app_engine.begin() as conn:
        # Deliberately do NOT call ``_set_scope``.
        rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id = :id"),
            {"id": item_id},
        ).all()
        assert rows == []

        # Sanity check: current_setting returns NULL (the ``true``
        # second arg makes it return NULL rather than raising).
        result = conn.execute(sa.text("SELECT current_setting('app.tenant_id', true) AS t")).scalar_one()
        assert result is None or result == ""


# ---------------------------------------------------------------------------
# Special-case table: canon_workspaces (id = app.workspace_id)
# ---------------------------------------------------------------------------


def test_canon_workspaces_self_visibility(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """The workspaces row policy matches its OWN id to the workspace GUC."""
    _seed_workspace(pg_admin_engine, workspace_id="ws-self-a", tenant_id="acme")
    _seed_workspace(pg_admin_engine, workspace_id="ws-self-b", tenant_id="acme")

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-self-a")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_workspaces WHERE id IN ('ws-self-a', 'ws-self-b')")
        ).all()
        # Only the row whose ``id`` matches ``app.workspace_id`` is
        # visible -- LIST workspaces is intentionally NOT a path that
        # works through RLS (the controller uses a BYPASSRLS role).
        assert {r.id for r in rows} == {"ws-self-a"}

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-self-b")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_workspaces WHERE id IN ('ws-self-a', 'ws-self-b')")
        ).all()
        assert {r.id for r in rows} == {"ws-self-b"}


# ---------------------------------------------------------------------------
# Special-case table: canon_agent_tokens (tenant-only)
# ---------------------------------------------------------------------------


def test_canon_agent_tokens_tenant_only_policy(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Same tenant -> both tokens visible regardless of workspace GUC.

    The token table has no workspace_id column (a token can serve
    multiple workspaces via ``workspace_allowlist_json``), so the
    policy intentionally filters on tenant only.
    """
    token_a = str(uuid.uuid4())
    token_b = str(uuid.uuid4())
    token_foreign = str(uuid.uuid4())
    _seed_agent_token(
        pg_admin_engine,
        token_id=token_a,
        tenant_id="acme",
        prefix=f"pref{token_a[:6]}",
        workspace_allowlist=["ws-a"],
    )
    _seed_agent_token(
        pg_admin_engine,
        token_id=token_b,
        tenant_id="acme",
        prefix=f"pref{token_b[:6]}",
        workspace_allowlist=["ws-b"],
    )
    _seed_agent_token(
        pg_admin_engine,
        token_id=token_foreign,
        tenant_id="bcorp",
        prefix=f"pref{token_foreign[:6]}",
        workspace_allowlist=None,
    )

    # Same tenant, ANY workspace GUC -> both acme tokens visible.
    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-a")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_agent_tokens WHERE id IN (:a, :b, :foreign)"),
            {"a": token_a, "b": token_b, "foreign": token_foreign},
        ).all()
        assert {r.id for r in rows} == {token_a, token_b}

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-b")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_agent_tokens WHERE id IN (:a, :b, :foreign)"),
            {"a": token_a, "b": token_b, "foreign": token_foreign},
        ).all()
        # Workspace GUC doesn't filter; both acme tokens still visible.
        assert {r.id for r in rows} == {token_a, token_b}

    # Foreign tenant -> only the foreign token visible.
    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="bcorp", workspace_id="ws-a")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_agent_tokens WHERE id IN (:a, :b, :foreign)"),
            {"a": token_a, "b": token_b, "foreign": token_foreign},
        ).all()
        assert {r.id for r in rows} == {token_foreign}


# ---------------------------------------------------------------------------
# Runtime-created table: canon_chunk_vectors. The 0013 migration's
# DO block guards with ``IF EXISTS``; on a fresh container the table
# doesn't exist when the migration runs, so production relies on
# PgvectorStore creating the table at boot AND a follow-up trigger
# applying the policy. For this test we mimic that flow: create the
# table, then re-run the policy DDL the same way the migration would.
# ---------------------------------------------------------------------------


def test_canon_chunk_vectors_runtime_table_isolation(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Boot the pgvector table at runtime, install the policy, verify isolation."""
    with pg_admin_engine.begin() as conn:
        # Mimic PgvectorStore._initialise_schema -- minimal version.
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(
            sa.text(
                """
                CREATE TABLE IF NOT EXISTS canon_chunk_vectors (
                    id           TEXT PRIMARY KEY,
                    namespace    TEXT NOT NULL DEFAULT 'default',
                    tenant_id    TEXT NOT NULL DEFAULT 'default',
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    embedding    vector(3) NOT NULL,
                    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
                    text         TEXT NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

        # Apply the same policy DDL the migration would have run if the
        # table had existed at migration time. The DO block in 0013 is
        # exactly this shape; we inline it here so production-style
        # bootstrap (table-first, policy-second) is exercised.
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
        # Make sure ``app_user`` can read/write it now that it exists.
        conn.execute(sa.text("GRANT ALL ON canon_chunk_vectors TO app_user"))

        # Seed two scopes.
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_chunk_vectors
                    (id, namespace, tenant_id, workspace_id, embedding, text)
                VALUES
                    ('vec-a', 't/acme/w/ws-a', 'acme', 'ws-a',
                     CAST('[1.0,0.0,0.0]' AS vector), 'alpha'),
                    ('vec-b', 't/acme/w/ws-b', 'acme', 'ws-b',
                     CAST('[0.0,1.0,0.0]' AS vector), 'bravo')
                ON CONFLICT (id) DO NOTHING
                """
            )
        )

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-a")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_chunk_vectors WHERE id IN ('vec-a', 'vec-b')")
        ).all()
        assert {r.id for r in rows} == {"vec-a"}

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-b")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_chunk_vectors WHERE id IN ('vec-a', 'vec-b')")
        ).all()
        assert {r.id for r in rows} == {"vec-b"}


# ---------------------------------------------------------------------------
# Inserts under RLS
# ---------------------------------------------------------------------------


def test_insert_into_own_scope_is_visible(
    pg_app_engine: sa.Engine,
) -> None:
    """Insert through ``app_user`` with matching GUCs -> visible afterward."""
    item_id = str(uuid.uuid4())
    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-insert-own")
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_knowledge_items
                    (id, tenant_id, workspace_id, status, current_version,
                     title, domain, jurisdiction, tags_json, metadata_json)
                VALUES
                    (:id, 'acme', 'ws-insert-own', 'active', 1,
                     'self-write', 'general', 'GLOBAL', '[]'::jsonb, '{}'::jsonb)
                """
            ),
            {"id": item_id},
        )
        rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id = :id"),
            {"id": item_id},
        ).all()
        assert {r.id for r in rows} == {item_id}


def test_insert_with_mismatched_scope_is_rejected(
    pg_app_engine: sa.Engine,
) -> None:
    """Cross-scope INSERTs are rejected -- Postgres auto-derives WITH CHECK from USING.

    Postgres docs: "If a WITH CHECK expression is not specified, then it
    is the same as USING expression." This means the 0013 migration's
    ``USING``-only policy auto-blocks INSERTs whose scope doesn't match
    the current GUCs. So ``app_user`` can't smuggle a row into another
    workspace -- the write fails with ``InsufficientPrivilege`` /
    ``new row violates row-level security policy``.

    This is the stronger property: not only is the read path safe, the
    write path is also locked down. We pin it here so a future
    migration that adds an explicit ``WITH CHECK`` (or, worse, an
    overbroad one) doesn't silently weaken this guarantee.
    """
    item_id = str(uuid.uuid4())
    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-reject-a")
        # Try to insert into ws-reject-b while the GUCs target ws-reject-a.
        with pytest.raises(sa.exc.ProgrammingError) as excinfo:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_knowledge_items
                        (id, tenant_id, workspace_id, status, current_version,
                         title, domain, jurisdiction, tags_json, metadata_json)
                    VALUES
                        (:id, 'acme', 'ws-reject-b', 'active', 1,
                         'rejected', 'general', 'GLOBAL', '[]'::jsonb, '{}'::jsonb)
                    """
                ),
                {"id": item_id},
            )
        assert "row-level security policy" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Multi-table coherence: one GUC pair, several scoped tables, every
# table respects the same scope window.
# ---------------------------------------------------------------------------


def test_multiple_scoped_tables_share_guc(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Setting the GUCs once filters every (tenant_id, workspace_id) table."""
    item_a = str(uuid.uuid4())
    item_b = str(uuid.uuid4())
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_a,
        tenant_id="acme",
        workspace_id="ws-multi-a",
        title="multi-a",
    )
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_b,
        tenant_id="acme",
        workspace_id="ws-multi-b",
        title="multi-b",
    )

    # Seed an audit event for each scope -- canon_audit_events also
    # carries the (tenant_id, workspace_id) scope and should be
    # filtered by the same GUC pair.
    audit_a = str(uuid.uuid4())
    audit_b = str(uuid.uuid4())
    with pg_admin_engine.begin() as conn:
        for row_id, ws in ((audit_a, "ws-multi-a"), (audit_b, "ws-multi-b")):
            conn.execute(
                sa.text(
                    """
                    INSERT INTO canon_audit_events
                        (id, tenant_id, workspace_id, event_type,
                         subject_id, subject_kind, actor, payload_json)
                    VALUES
                        (:id, 'acme', :ws, 'test.action',
                         :id, 'test', 'test-actor', '{}'::jsonb)
                    """
                ),
                {"id": row_id, "ws": ws},
            )

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-multi-a")
        item_rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id IN (:a, :b)"),
            {"a": item_a, "b": item_b},
        ).all()
        audit_rows = conn.execute(
            sa.text("SELECT id FROM canon_audit_events WHERE id IN (:a, :b)"),
            {"a": audit_a, "b": audit_b},
        ).all()
        assert {r.id for r in item_rows} == {item_a}
        assert {r.id for r in audit_rows} == {audit_a}


# ---------------------------------------------------------------------------
# SET LOCAL hygiene: scope set in one transaction is gone in the next
# (this is the basic Postgres semantic, but it's the property the
# session-factory hook depends on -- if SET LOCAL ever bled, the
# whole isolation guarantee would collapse).
# ---------------------------------------------------------------------------


def test_set_local_does_not_bleed_across_transactions(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """``SET LOCAL`` is per-txn; a fresh txn without it sees zero rows."""
    item_id = str(uuid.uuid4())
    _seed_knowledge_item(
        pg_admin_engine,
        item_id=item_id,
        tenant_id="acme",
        workspace_id="ws-bleed",
        title="leak-test",
    )

    with pg_app_engine.begin() as conn:
        _set_scope(conn, tenant_id="acme", workspace_id="ws-bleed")
        rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id = :id"),
            {"id": item_id},
        ).all()
        assert {r.id for r in rows} == {item_id}

    # Fresh transaction -- no SET LOCAL means current_setting returns
    # NULL again, policy can't match, zero rows.
    with pg_app_engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT id FROM canon_knowledge_items WHERE id = :id"),
            {"id": item_id},
        ).all()
        assert rows == []


# ---------------------------------------------------------------------------
# FORCE RLS: confirm that ``app_user`` cannot bypass even if it owned
# the row. Postgres exempts table owners from RLS by default; ``FORCE
# ROW LEVEL SECURITY`` removes that exemption. This test confirms the
# migration set FORCE for at least one representative table.
# ---------------------------------------------------------------------------


def test_force_rls_applies_to_table_owner_path(
    pg_admin_engine: sa.Engine,
    pg_app_engine: sa.Engine,
) -> None:
    """Even with FORCE off, app_user isn't the owner, so the policy applies.

    What this really pins is that the migration left FORCE on; we
    introspect ``pg_class.relforcerowsecurity`` directly to confirm
    the migration ran the ``FORCE ROW LEVEL SECURITY`` step on the
    representative scoped table.
    """
    with pg_app_engine.begin() as conn:
        force_on = conn.execute(
            sa.text(
                """
                SELECT relforcerowsecurity
                FROM pg_class
                WHERE relname = 'canon_knowledge_items'
                """
            )
        ).scalar_one()
        assert force_on is True

        # And on canon_workspaces (special-case policy) too.
        force_on_ws = conn.execute(
            sa.text(
                """
                SELECT relforcerowsecurity
                FROM pg_class
                WHERE relname = 'canon_workspaces'
                """
            )
        ).scalar_one()
        assert force_on_ws is True
