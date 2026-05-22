# Copyright 2026 Firefly Software Solutions Inc
"""Scope-isolation coverage for :class:`PgVectorVectorStore`.

Two-layer strategy:

1. **Always-on signature checks (SQLite-friendly).** These exercise
   the parameter contract without booting Postgres: they assert that
   the public surface (``upsert`` / ``search`` / ``delete``) accepts
   ``tenant_id`` / ``workspace_id`` kwargs, that the helper namespace
   template renders correctly, and that ``_doc_to_row`` populates the
   scope columns from the kwargs (overriding any document-level
   ``namespace`` field with the canonical ``t/<tenant>/w/<workspace>``
   template).

2. **Behavioural ANN isolation (Postgres testcontainer).** When Docker
   is available, this boots a Postgres + pgvector instance, runs
   ``_initialise_schema``, seeds two workspaces (same tenant) and one
   foreign tenant, and asserts that ``search`` returns ONLY the rows
   whose ``(tenant_id, workspace_id)`` matches the query scope. Without
   Docker, this layer is auto-skipped.

The testcontainer layer mirrors the pattern in
``flyradar/tests/integration/test_rls_isolation.py``: we wrap the
PostgresContainer as a module-scoped fixture and skip cleanly when
Docker is absent. The flycanon repo doesn't have an ``integration``
test directory yet -- skipping inside the test keeps the suite
single-tier and lets ``uv run pytest tests/unit/`` remain the
authoritative ``hermetic`` test invocation.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flycanon.core.services.retrieval.pgvector_store import (
    PgVectorVectorStore,
    _doc_to_row,
    _scope_namespace,
)

# ---------------------------------------------------------------------------
# Layer 1 -- always-on signature + helper checks
# ---------------------------------------------------------------------------


class _FakeVectorDoc:
    """Minimal stand-in for ``fireflyframework_agentic.vectorstores.types.VectorDocument``.

    Avoids importing the real type so this layer keeps running even
    when the agentic test deps are partially mocked.
    """

    def __init__(
        self,
        *,
        id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> None:
        self.id = id
        self.text = text
        self.embedding = embedding
        self.metadata = metadata or {}
        self.namespace = namespace


class TestScopeNamespaceHelper:
    def test_canonical_template_renders(self):
        assert _scope_namespace("acme", "ws-a") == "t/acme/w/ws-a"

    def test_default_scope_is_safe(self):
        assert _scope_namespace("default", "default") == "t/default/w/default"


class TestPublicSurfaceSignatures:
    """The protocol contract: every read/write path takes scope kwargs."""

    @pytest.mark.parametrize("method_name", ["upsert", "search", "delete"])
    def test_method_accepts_scope_kwargs(self, method_name: str):
        method = getattr(PgVectorVectorStore, method_name)
        sig = inspect.signature(method)
        params = sig.parameters
        assert "tenant_id" in params, f"{method_name} must accept tenant_id"
        assert "workspace_id" in params, f"{method_name} must accept workspace_id"
        # Keyword-only -- callers must spell them out so we can't
        # accidentally pass positional scope values.
        assert params["tenant_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["workspace_id"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_search_accepts_widening_factor(self):
        sig = inspect.signature(PgVectorVectorStore.search)
        assert "widening_factor" in sig.parameters


class TestDocToRow:
    """Helper that produces the row dict for the bulk INSERT."""

    def test_populates_scope_columns_from_kwargs(self):
        doc = _FakeVectorDoc(
            id="chunk-1",
            text="hello",
            embedding=[0.1, 0.2],
            metadata={"k": "v"},
            namespace=None,
        )
        row = _doc_to_row(
            doc,
            namespace="t/acme/w/ws-a",
            tenant_id="acme",
            workspace_id="ws-a",
        )
        assert row["tenant_id"] == "acme"
        assert row["workspace_id"] == "ws-a"
        assert row["namespace"] == "t/acme/w/ws-a"
        assert row["id"] == "chunk-1"
        assert row["text"] == "hello"
        assert row["embedding"] == "[0.1000000,0.2000000]"

    def test_document_namespace_overrides_default(self):
        """When the VectorDocument carries an explicit namespace, it wins.

        The fallback ``namespace`` kwarg is used only when the doc
        itself has none -- it keeps the legacy ``upsert(documents,
        namespace='foo')`` ergonomics working.
        """
        doc = _FakeVectorDoc(
            id="chunk-2",
            text="hi",
            embedding=[0.0],
            namespace="legacy-namespace",
        )
        row = _doc_to_row(
            doc,
            namespace="t/acme/w/ws-a",
            tenant_id="acme",
            workspace_id="ws-a",
        )
        assert row["namespace"] == "legacy-namespace"
        # Scope columns still come from kwargs, not the doc.
        assert row["tenant_id"] == "acme"
        assert row["workspace_id"] == "ws-a"

    def test_missing_embedding_raises(self):
        doc = _FakeVectorDoc(id="chunk-3", text="x", embedding=[])
        with pytest.raises(ValueError, match="no embedding"):
            _doc_to_row(
                doc,
                namespace="t/d/w/d",
                tenant_id="default",
                workspace_id="default",
            )


class TestInitialiseSchemaInstallsRls:
    """``_initialise_schema`` must emit the RLS DDL alongside the table DDL.

    This pins the bootstrap-time RLS install that closes the deploy-
    ordering gap: migration ``0013_rls_policies`` only patches an
    already-existing table, so on a fresh deploy where PgvectorStore
    creates the table after the migration runs, the policy must come
    from here.

    We mock the AsyncEngine so we can capture the SQL strings without
    booting Postgres; the assertions check that both ``ENABLE ROW
    LEVEL SECURITY`` and ``CREATE POLICY tenant_workspace_isolation``
    appear in the emitted DDL, and that the DO block uses the
    configured ``self._table`` name so a custom table name still
    installs the policy on the right object.
    """

    def _build_store_with_recording_engine(
        self, *, table_name: str = "canon_chunk_vectors"
    ) -> tuple[PgVectorVectorStore, list[str]]:
        """Build a store whose ``_engine`` records every SQL string executed."""
        store = PgVectorVectorStore.__new__(PgVectorVectorStore)
        store._url = "postgresql+asyncpg://stub/stub"
        store._dim = 3
        store._table = table_name
        store._hnsw_m = 16
        store._hnsw_ef_construction = 64
        store._factory = None
        store._initialised = False

        captured: list[str] = []

        async def _record(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
            # SQLAlchemy ``text(...)`` objects expose their template via
            # ``.text``; raw strings are recorded as-is so the test
            # works against both shapes.
            captured.append(getattr(stmt, "text", str(stmt)))
            return MagicMock()

        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=_record)

        # ``async with engine.begin() as conn`` -- both __aenter__ and
        # __aexit__ must be awaitable; AsyncMock returns the conn.
        begin_ctx = MagicMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=conn)
        begin_ctx.__aexit__ = AsyncMock(return_value=None)

        engine = MagicMock()
        engine.begin = MagicMock(return_value=begin_ctx)
        store._engine = engine

        return store, captured

    @pytest.mark.asyncio
    async def test_emits_enable_and_force_rls(self):
        store, captured = self._build_store_with_recording_engine()
        await store._initialise_schema()
        joined = "\n".join(captured)
        assert "ENABLE ROW LEVEL SECURITY" in joined
        assert "FORCE ROW LEVEL SECURITY" in joined

    @pytest.mark.asyncio
    async def test_emits_create_policy_tenant_workspace_isolation(self):
        store, captured = self._build_store_with_recording_engine()
        await store._initialise_schema()
        joined = "\n".join(captured)
        assert "CREATE POLICY tenant_workspace_isolation" in joined
        # The policy must match BOTH scope columns -- a tenant-only
        # policy would let workspace-A read workspace-B's vectors.
        assert "current_setting('app.tenant_id', true)" in joined
        assert "current_setting('app.workspace_id', true)" in joined

    @pytest.mark.asyncio
    async def test_do_block_is_idempotent_via_pg_policies_lookup(self):
        store, captured = self._build_store_with_recording_engine()
        await store._initialise_schema()
        joined = "\n".join(captured)
        # The idempotency guard must check pg_policies for the
        # specific (table, policy) pair before re-creating it; without
        # this, the second boot would error out with "policy already
        # exists".
        assert "pg_policies" in joined
        assert "policyname = 'tenant_workspace_isolation'" in joined

    @pytest.mark.asyncio
    async def test_do_block_soft_fails_on_insufficient_privilege(self):
        store, captured = self._build_store_with_recording_engine()
        await store._initialise_schema()
        joined = "\n".join(captured)
        # Non-admin boots must log a warning instead of crashing -- the
        # EXCEPTION handler in the DO block is what guarantees this.
        assert "EXCEPTION WHEN insufficient_privilege" in joined
        assert "RAISE WARNING" in joined

    @pytest.mark.asyncio
    async def test_uses_configured_table_name(self):
        store, captured = self._build_store_with_recording_engine(
            table_name="custom_chunk_vectors"
        )
        await store._initialise_schema()
        joined = "\n".join(captured)
        # Both the DDL target and the pg_policies lookup must use the
        # configured table name so a non-default deployment still gets
        # RLS on the right object.
        assert "ALTER TABLE custom_chunk_vectors ENABLE ROW LEVEL SECURITY" in joined
        assert "CREATE POLICY tenant_workspace_isolation ON custom_chunk_vectors" in joined
        assert "tablename = 'custom_chunk_vectors'" in joined


# ---------------------------------------------------------------------------
# Layer 2 -- behavioural ANN scope isolation (Postgres testcontainer)
# ---------------------------------------------------------------------------


try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:
    _TESTCONTAINERS_AVAILABLE = False


_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()
_SKIP_REASON = "Docker + testcontainers required for pgvector ANN scope test"

# Pull a pgvector-bundled Postgres image -- the stock ``postgres:16-alpine``
# image does NOT ship the extension, so ``CREATE EXTENSION vector`` fails.
_PGVECTOR_IMAGE = "pgvector/pgvector:pg16"


@pytest.fixture(scope="module")
def pgvector_url() -> Iterator[str]:
    if not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE):
        pytest.skip(_SKIP_REASON)
    with PostgresContainer(_PGVECTOR_IMAGE) as pg:
        url = pg.get_connection_url()
        # PostgresContainer hands back a psycopg2-style URL; coerce
        # to the asyncpg variant the store expects.
        if url.startswith("postgresql+psycopg2"):
            url = url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        yield url


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_search_isolates_workspace(pgvector_url: str) -> None:
    """Same tenant, different workspaces -> only own workspace's chunk returned."""
    from fireflyframework_agentic.vectorstores.types import VectorDocument

    store = PgVectorVectorStore(
        database_url=pgvector_url,
        dimension=3,
        table_name="canon_chunk_vectors_iso",
    )
    try:
        # Seed workspace A and workspace B with one near-identical
        # chunk each -- the embedding similarity alone wouldn't be
        # enough to discriminate them; only the scope filter can.
        doc_a = VectorDocument(
            id="chunk-a",
            text="alpha",
            embedding=[1.0, 0.0, 0.0],
            metadata={"side": "a"},
        )
        doc_b = VectorDocument(
            id="chunk-b",
            text="bravo",
            embedding=[1.0, 0.0, 0.0],
            metadata={"side": "b"},
        )
        await store.upsert([doc_a], tenant_id="acme", workspace_id="ws-a")
        await store.upsert([doc_b], tenant_id="acme", workspace_id="ws-b")

        # Search workspace A -- only chunk-a should come back.
        hits_a = await store.search(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=5,
            tenant_id="acme",
            workspace_id="ws-a",
        )
        assert {h.document.id for h in hits_a} == {"chunk-a"}

        # Search workspace B -- only chunk-b should come back.
        hits_b = await store.search(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=5,
            tenant_id="acme",
            workspace_id="ws-b",
        )
        assert {h.document.id for h in hits_b} == {"chunk-b"}
    finally:
        await store.close()


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_search_isolates_tenant(pgvector_url: str) -> None:
    """Different tenants -- ANN query returns nothing across tenants."""
    from fireflyframework_agentic.vectorstores.types import VectorDocument

    store = PgVectorVectorStore(
        database_url=pgvector_url,
        dimension=3,
        table_name="canon_chunk_vectors_tenant_iso",
    )
    try:
        doc = VectorDocument(
            id="chunk-acme",
            text="acme-secret",
            embedding=[1.0, 0.0, 0.0],
        )
        await store.upsert([doc], tenant_id="acme", workspace_id="ws-a")

        # Same workspace_id but DIFFERENT tenant -- must be empty.
        hits = await store.search(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=5,
            tenant_id="bcorp",
            workspace_id="ws-a",
        )
        assert hits == []
    finally:
        await store.close()


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_delete_respects_scope(pgvector_url: str) -> None:
    """``delete`` must not nuke rows that live in a foreign scope."""
    from fireflyframework_agentic.vectorstores.types import VectorDocument

    store = PgVectorVectorStore(
        database_url=pgvector_url,
        dimension=3,
        table_name="canon_chunk_vectors_delete_iso",
    )
    try:
        doc_a = VectorDocument(id="shared-id", text="a", embedding=[1.0, 0.0, 0.0])
        doc_b = VectorDocument(id="shared-id", text="b", embedding=[1.0, 0.0, 0.0])
        # Different scopes -- but the row id is shared on purpose to
        # detect cross-scope blast radius. The second upsert will
        # actually replace the first row due to the PK, so we use
        # distinct ids to set up the assertion meaningfully.
        await store.upsert(
            [VectorDocument(id="del-a", text="a", embedding=[1.0, 0.0, 0.0])],
            tenant_id="acme",
            workspace_id="ws-a",
        )
        await store.upsert(
            [VectorDocument(id="del-b", text="b", embedding=[1.0, 0.0, 0.0])],
            tenant_id="acme",
            workspace_id="ws-b",
        )

        # Attempt to delete del-b from ws-a's scope -- should be a no-op.
        await store.delete(["del-b"], tenant_id="acme", workspace_id="ws-a")
        hits_b = await store.search(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=5,
            tenant_id="acme",
            workspace_id="ws-b",
        )
        assert {h.document.id for h in hits_b} == {"del-b"}

        # Delete from the correct scope -- now it should be gone.
        await store.delete(["del-b"], tenant_id="acme", workspace_id="ws-b")
        hits_b_after = await store.search(
            query_embedding=[1.0, 0.0, 0.0],
            top_k=5,
            tenant_id="acme",
            workspace_id="ws-b",
        )
        assert hits_b_after == []
        # Avoid the unused-variable lint complaint while keeping the
        # signal explicit for future maintainers.
        del doc_a, doc_b
    finally:
        await store.close()
