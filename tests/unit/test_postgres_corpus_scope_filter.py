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

"""Scope-isolation coverage for :class:`PostgresCorpus`.

Two-layer strategy mirroring ``test_pgvector_scope_isolation.py``:

1. **Always-on signature checks (SQLite-friendly).** Assert the public
   surface (``bm25_search`` / ``get_chunks``) accepts ``tenant_id`` /
   ``workspace_id`` keyword-only kwargs. The contract is what callers
   (RetrievalService, IndexService) bind against, so it must hold
   regardless of whether Docker is around to run the behavioural
   layer.

2. **Behavioural BM25 isolation (Postgres testcontainer).** When
   Docker is available, this boots a Postgres instance (stock
   ``postgres:16-alpine`` -- BM25 rides on ``tsvector`` + GIN which
   are core Postgres, no pgvector extension needed), runs Alembic
   ``upgrade head`` to materialise the full schema (canon_chunks +
   GENERATED ``tsv`` + scope columns + scope indexes from migration
   0009), seeds raw rows in two workspaces / two tenants, and asserts
   :meth:`PostgresCorpus.bm25_search` only returns hits from the
   queried ``(tenant_id, workspace_id)`` scope. Without Docker, this
   layer is auto-skipped.

We intentionally seed via raw SQL rather than going through
``ChunkRepository`` so the test stays focused on the BM25 surface; a
foreign-scope row that bleeds into the result would be a regression in
the SQL ``WHERE`` clause, not in the ORM.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from flycanon.core.services.retrieval.postgres_corpus import PostgresCorpus

# ---------------------------------------------------------------------------
# Layer 1 -- always-on signature checks
# ---------------------------------------------------------------------------


class TestPublicSurfaceSignatures:
    """The protocol contract: every read path takes scope kwargs."""

    @pytest.mark.parametrize("method_name", ["bm25_search", "get_chunks"])
    def test_method_accepts_scope_kwargs(self, method_name: str):
        method = getattr(PostgresCorpus, method_name)
        sig = inspect.signature(method)
        params = sig.parameters
        assert "tenant_id" in params, f"{method_name} must accept tenant_id"
        assert "workspace_id" in params, f"{method_name} must accept workspace_id"
        # Keyword-only -- callers must spell them out so we cannot
        # accidentally pass positional scope values (mirrors the
        # PgVectorVectorStore convention).
        assert params["tenant_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["workspace_id"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_bm25_search_keeps_top_k_kwarg(self):
        """``top_k`` must stay keyword-only and live next to the scope kwargs."""
        sig = inspect.signature(PostgresCorpus.bm25_search)
        assert "top_k" in sig.parameters
        assert sig.parameters["top_k"].kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Layer 2 -- behavioural BM25 scope isolation (Postgres testcontainer)
# ---------------------------------------------------------------------------


try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]

    _TESTCONTAINERS_AVAILABLE = True
except ImportError:
    _TESTCONTAINERS_AVAILABLE = False


_DOCKER_AVAILABLE = bool(os.environ.get("DOCKER_HOST")) or Path("/var/run/docker.sock").exists()
_SKIP_REASON = "Docker + testcontainers required for PostgresCorpus BM25 scope test"

# Stock postgres image is fine: BM25 only needs tsvector + GIN, both
# core Postgres features. (Unlike the pgvector test, which needs the
# ``vector`` extension and so pulls the pgvector-bundled image.)
_POSTGRES_IMAGE = "postgres:16-alpine"


def _alembic_cfg(db_url: str) -> Config:
    """Build an Alembic config pointing at ``db_url``.

    Used to run ``upgrade head`` against the testcontainer so the
    BM25 query has the GENERATED ``tsv`` column from migration
    0003 and the scope columns + indexes from migrations 0008 / 0009.
    """
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Boot Postgres + run all migrations; yield the asyncpg URL."""
    if not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE):
        pytest.skip(_SKIP_REASON)
    with PostgresContainer(_POSTGRES_IMAGE) as pg:
        # PostgresContainer hands back a psycopg2-style URL by
        # default; alembic env.py rewrites ``+asyncpg`` -> ``+psycopg``
        # internally so we can feed it the async variant directly.
        async_url = pg.get_connection_url()
        if async_url.startswith("postgresql+psycopg2"):
            async_url = async_url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
        elif async_url.startswith("postgresql://"):
            async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        command.upgrade(_alembic_cfg(async_url), "head")
        yield async_url


def _seed_chunk(
    sync_url: str,
    *,
    chunk_id: str,
    source_id: str,
    content: str,
    tenant_id: str,
    workspace_id: str,
) -> None:
    """Insert a source + a single chunk with explicit scope columns.

    The ``tsv`` column is GENERATED so we don't write to it; the
    insert into ``content`` is enough for the BM25 projection to
    populate atomically in the same transaction.
    """
    engine = sa.create_engine(sync_url)
    with engine.begin() as conn:
        # canon_sources is the FK parent; we need a row so the join in
        # bm25_search has something to bind to.
        # The unique constraint on content_sha256 enforces a real
        # 64-char hex digest -- use the source_id hash so each seeded
        # row has a distinct digest and the FTS / scope queries don't
        # collide on it.
        sha = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_sources
                    (id, kind, status, filename, content_sha256,
                     content_bytes, n_chunks, tenant_id, workspace_id)
                VALUES
                    (:id, 'text', 'ingested', :filename, :sha,
                     0, 1, :tenant_id, :workspace_id)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": source_id,
                "filename": f"{source_id}.txt",
                "sha": sha,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            },
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_chunks
                    (id, source_id, index_in_source, total_chunks, content,
                     char_start, char_end, tenant_id, workspace_id)
                VALUES
                    (:id, :source_id, 0, 1, :content,
                     0, :char_end, :tenant_id, :workspace_id)
                """
            ),
            {
                "id": chunk_id,
                "source_id": source_id,
                "content": content,
                "char_end": len(content),
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            },
        )
    engine.dispose()


def _sync_url(async_url: str) -> str:
    return async_url.replace("+asyncpg", "+psycopg")


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_bm25_search_isolates_workspace(postgres_url: str) -> None:
    """Same tenant, different workspaces -> hits only from the queried scope."""
    sync_url = _sync_url(postgres_url)
    # Bare UUIDs only -- canon_*.id is VARCHAR(36) which exactly fits a
    # 36-char UUID string; the per-test scope is enforced through the
    # tenant_id / workspace_id columns rather than ID prefixes.
    chunk_a = str(uuid.uuid4())
    chunk_b = str(uuid.uuid4())
    source_a = str(uuid.uuid4())
    source_b = str(uuid.uuid4())
    _seed_chunk(
        sync_url,
        chunk_id=chunk_a,
        source_id=source_a,
        content="alpha test workspace a",
        tenant_id="acme",
        workspace_id="ws-a",
    )
    _seed_chunk(
        sync_url,
        chunk_id=chunk_b,
        source_id=source_b,
        content="alpha test workspace b",
        tenant_id="acme",
        workspace_id="ws-b",
    )

    corpus = PostgresCorpus(database_url=postgres_url)
    try:
        # Query ws-a -- only chunk-a should come back.
        hits_a = await corpus.bm25_search("alpha", top_k=10, tenant_id="acme", workspace_id="ws-a")
        assert {h.chunk_id for h in hits_a} == {chunk_a}

        # Query ws-b -- only chunk-b.
        hits_b = await corpus.bm25_search("alpha", top_k=10, tenant_id="acme", workspace_id="ws-b")
        assert {h.chunk_id for h in hits_b} == {chunk_b}

        # Query a workspace with no rows -- empty.
        hits_c = await corpus.bm25_search("alpha", top_k=10, tenant_id="acme", workspace_id="ws-c")
        assert hits_c == []
    finally:
        await corpus.close()


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_bm25_search_isolates_tenant(postgres_url: str) -> None:
    """Cross-tenant queries return zero hits even when the workspace_id matches."""
    sync_url = _sync_url(postgres_url)
    chunk_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    _seed_chunk(
        sync_url,
        chunk_id=chunk_id,
        source_id=source_id,
        content="alpha tenant secret",
        tenant_id="acme",
        workspace_id="ws-a",
    )

    corpus = PostgresCorpus(database_url=postgres_url)
    try:
        # Foreign tenant + same workspace_id slug -> empty.
        hits = await corpus.bm25_search("alpha", top_k=10, tenant_id="bcorp", workspace_id="ws-a")
        assert hits == []
    finally:
        await corpus.close()


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_get_chunks_respects_scope(postgres_url: str) -> None:
    """``get_chunks`` must not hydrate a chunk that lives in a foreign scope.

    Prevents the leak where a caller has the *id* of a foreign chunk
    (e.g. via a stale cache or a malicious request) and asks for its
    content -- the SQL scope filter rejects it.
    """
    sync_url = _sync_url(postgres_url)
    chunk_a = str(uuid.uuid4())
    chunk_b = str(uuid.uuid4())
    source_a = str(uuid.uuid4())
    source_b = str(uuid.uuid4())
    _seed_chunk(
        sync_url,
        chunk_id=chunk_a,
        source_id=source_a,
        content="hydrate me workspace a",
        tenant_id="acme",
        workspace_id="ws-a",
    )
    _seed_chunk(
        sync_url,
        chunk_id=chunk_b,
        source_id=source_b,
        content="hydrate me workspace b",
        tenant_id="acme",
        workspace_id="ws-b",
    )

    corpus = PostgresCorpus(database_url=postgres_url)
    try:
        # Ask for BOTH ids from ws-a's scope -- only chunk_a comes back.
        rows = await corpus.get_chunks([chunk_a, chunk_b], tenant_id="acme", workspace_id="ws-a")
        assert {r.chunk_id for r in rows} == {chunk_a}

        # Foreign tenant -- empty even for an id that exists.
        rows_foreign = await corpus.get_chunks([chunk_a], tenant_id="bcorp", workspace_id="ws-a")
        assert rows_foreign == []
    finally:
        await corpus.close()


# ---------------------------------------------------------------------------
# Row-level security under a NOBYPASSRLS role. The three tests above run as
# the container's superuser, which BYPASSES RLS, so they would pass even if
# the corpus never set a GUC -- and until 26.7.1 it did not: ``bm25_search``
# ran on a bare Core connection where the ORM's ``after_begin`` listener
# never fires, and under FORCE RLS the BM25 channel returned zero rows for a
# ``flycanon_app``-shaped role (found by the dworkers programme on
# 2026-09-17). These tests mint that role and prove the corpus binds
# ``app.tenant_id`` / ``app.workspace_id`` itself.
# ---------------------------------------------------------------------------


def _mint_app_role(admin_sync_url: str) -> None:
    """Create the non-bypass ``corpus_app`` LOGIN role with plain DML grants.

    Mirrors the ``flycanon_app`` posture of a shared deployment: LOGIN,
    NOSUPERUSER, NOBYPASSRLS, ``USAGE`` on the schema and DML on every
    table -- exactly what ``docs/deployment.md`` "RLS roles" prescribes.
    """
    engine = sa.create_engine(admin_sync_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        # Idempotent on purpose: a role that already holds grants cannot be
        # DROPped (its privileges are dependencies), and the module-scoped
        # container serves several tests that each mint the same role.
        conn.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'corpus_app') THEN
                        CREATE ROLE corpus_app LOGIN PASSWORD 'app' NOSUPERUSER NOBYPASSRLS;
                    END IF;
                END
                $$;
                """
            )
        )
        conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO corpus_app"))
        conn.execute(
            sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO corpus_app")
        )
        conn.execute(sa.text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO corpus_app"))
    engine.dispose()


def _as_app_role(async_url: str) -> str:
    """Return *async_url* with the credentials swapped for ``corpus_app``."""
    scheme, rest = async_url.split("://", 1)
    _, host_part = rest.split("@", 1)
    return f"{scheme}://corpus_app:app@{host_part}"


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_bm25_search_returns_rows_under_a_non_bypass_role(postgres_url: str) -> None:
    """``bm25_search`` and ``get_chunks`` find the scope's rows as a NOBYPASSRLS role.

    The seeded chunk is inserted as the superuser; the corpus reads it as
    ``corpus_app``. Without the GUC binding in
    :meth:`PostgresCorpus._scoped_connection` FORCE RLS on ``canon_chunks``
    and ``canon_sources`` hides every row and both calls return ``[]``.
    """
    sync_url = _sync_url(postgres_url)
    _mint_app_role(sync_url)
    chunk_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    _seed_chunk(
        sync_url,
        chunk_id=chunk_id,
        source_id=source_id,
        content="the daily meal allowance abroad is seventy euros",
        tenant_id="acme",
        workspace_id="ws-rls",
    )

    corpus = PostgresCorpus(database_url=_as_app_role(postgres_url))
    try:
        hits = await corpus.bm25_search("allowance abroad", top_k=10, tenant_id="acme", workspace_id="ws-rls")
        assert [h.chunk_id for h in hits] == [chunk_id]
        assert hits[0].doc_id == source_id  # the JOIN on canon_sources passed RLS too

        rows = await corpus.get_chunks([chunk_id], tenant_id="acme", workspace_id="ws-rls")
        assert [r.chunk_id for r in rows] == [chunk_id]

        # The policy still fails closed for a foreign scope: the GUCs name
        # bcorp, the WHERE clause names bcorp, and neither matches the row.
        assert (
            await corpus.bm25_search("allowance abroad", top_k=10, tenant_id="bcorp", workspace_id="ws-rls")
            == []
        )
        assert await corpus.get_chunks([chunk_id], tenant_id="bcorp", workspace_id="ws-rls") == []
    finally:
        await corpus.close()


@pytest.mark.skipif(
    not (_TESTCONTAINERS_AVAILABLE and _DOCKER_AVAILABLE),
    reason=_SKIP_REASON,
)
@pytest.mark.asyncio
async def test_bare_connection_without_gucs_sees_nothing_as_the_app_role(postgres_url: str) -> None:
    """The control: the same SQL on a bare connection, no GUCs, returns no row.

    This is the pre-26.7.1 shape of ``bm25_search`` (``engine.connect()``
    with the WHERE clause alone). It pins WHY the GUC binding exists: RLS
    fails closed, so a scoped read that forgets the GUCs is not "less
    isolated", it is empty -- and the assertion above would regress to
    ``[]`` the day someone drops :meth:`PostgresCorpus._scoped_connection`.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    sync_url = _sync_url(postgres_url)
    _mint_app_role(sync_url)
    chunk_id = str(uuid.uuid4())
    _seed_chunk(
        sync_url,
        chunk_id=chunk_id,
        source_id=str(uuid.uuid4()),
        content="invisible without the tenant guc",
        tenant_id="acme",
        workspace_id="ws-bare",
    )
    engine = create_async_engine(_as_app_role(postgres_url))
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT c.id FROM canon_chunks c JOIN canon_sources s ON s.id = c.source_id "
                    "WHERE c.tenant_id = :t AND c.workspace_id = :w AND c.id = :id"
                ),
                {"t": "acme", "w": "ws-bare", "id": chunk_id},
            )
            assert result.all() == []
            # ... and the GUC really is unset on this connection.
            guc = (await conn.execute(sa.text("SELECT current_setting('app.tenant_id', true)"))).scalar_one()
            assert guc in (None, "")
    finally:
        await engine.dispose()
