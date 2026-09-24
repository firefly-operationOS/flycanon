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

"""The suite's search, end to end, as a ``NOBYPASSRLS`` role -- and it finds rows.

This is the test the dworkers programme asked for on 2026-09-17, after
measuring that flycanon 26.7.1 served as ``flycanon_app`` answered
``hits: []`` to every ``/search`` while the same request as the database
owner found the chunk. Two defects hid behind that empty list, and this
module pins both:

1. The BM25 corpus ran on a bare Core connection where the ORM's
   ``after_begin`` listener never fires, so under FORCE RLS the lexical
   channel saw no rows and RRF fused nothing with the vector channel's hit
   (:meth:`PostgresCorpus._scoped_connection` now binds the GUCs itself).
2. The dense store ran ``CREATE TABLE / INDEX IF NOT EXISTS`` at boot, which
   PostgreSQL refuses for a role without ``CREATE`` on the schema and without
   ownership -- before it honours the guard (migration ``0016`` creates the
   table; :meth:`RlsPgVectorVectorStore._create_schema` is verify-first).

The shape is the production one: ``build_corpus_context`` builds the real
:class:`PostgresCorpus` + :class:`RlsPgVectorVectorStore` (inside the
framework's :class:`TenantScopedVectorStore`), the real repositories hydrate
through ORM sessions under a bound :class:`TenantContext` (what the
middleware does per request), and :class:`RetrievalService.search` runs the
fused retrieval. Only the embedder is a fake (one fixed unit vector), because
the point is the database, not the model. Everything runs as ``app_user``:
LOGIN, NOSUPERUSER, NOBYPASSRLS, USAGE on the schema, DML on the tables,
NO CREATE -- the ``docs/deployment.md`` "RLS roles" posture.

Skip behaviour follows ``tests/integration/conftest.py`` (Docker +
testcontainers, the pgvector image).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fireflyframework_agentic.vectorstores import VectorDocument

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

#: The embedding width the migration was run with (``FLYCANON_EMBEDDING_DIMENSIONS``
#: is set by the module fixture below BEFORE alembic runs 0016 in the module
#: container, so the column is created at this width). 64 is the settings floor.
DIMENSION = 64
UNIT_VECTOR = [1.0] + [0.0] * (DIMENSION - 1)
TENANT = "acme"
WORKSPACE = "ws-search"


@pytest.fixture(scope="module", autouse=True)
def _dimension_for_the_migration():
    """Size the migration's ``vector(N)`` column for this suite.

    Migration 0016 reads ``FLYCANON_EMBEDDING_DIMENSIONS`` through
    :func:`flycanon.config.get_settings` when it runs; the ``pg_container``
    fixture (``conftest.py``) runs ``alembic upgrade head`` on first use, so
    the variable must be in the environment before that fixture is requested.
    The cached settings object is cleared on both sides so a value from an
    earlier module cannot leak in either direction.
    """
    from flycanon.config import get_settings

    previous = os.environ.get("FLYCANON_EMBEDDING_DIMENSIONS")
    os.environ["FLYCANON_EMBEDDING_DIMENSIONS"] = str(DIMENSION)
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FLYCANON_EMBEDDING_DIMENSIONS", None)
        else:
            os.environ["FLYCANON_EMBEDDING_DIMENSIONS"] = previous
        get_settings.cache_clear()


def _async_url(pg_container, *, user: str | None = None, password: str | None = None) -> str:  # type: ignore[no-untyped-def]
    url = pg_container.get_connection_url()
    if url.startswith("postgresql+psycopg2"):
        url = url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if user is None:
        return url
    scheme, rest = url.split("://", 1)
    _, host_part = rest.split("@", 1)
    return f"{scheme}://{user}:{password}@{host_part}"


def _seed_source_and_chunk(
    admin_engine: sa.Engine,
    *,
    source_id: str,
    chunk_id: str,
    content: str,
    title: str,
    tenant_id: str = TENANT,
    workspace_id: str = WORKSPACE,
) -> None:
    """Insert a workspace, a source (with a title) and one chunk as the admin role."""
    with admin_engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_workspaces (id, tenant_id, name, status)
                VALUES (:id, :tenant_id, :name, 'active')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": workspace_id, "tenant_id": tenant_id, "name": f"{tenant_id}/{workspace_id}"},
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_sources
                    (id, kind, status, filename, content_sha256, content_bytes, n_chunks,
                     tenant_id, workspace_id, metadata_json)
                VALUES
                    (:id, 'text', 'ingested', :filename, :sha, :bytes, 1,
                     :tenant_id, :workspace_id, CAST(:metadata AS JSON))
                """
            ),
            {
                "id": source_id,
                "filename": f"{title}.txt",
                "sha": hashlib.sha256(source_id.encode()).hexdigest(),
                "bytes": len(content),
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "metadata": f'{{"title": "{title}"}}',
            },
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO canon_chunks
                    (id, source_id, index_in_source, total_chunks, content,
                     char_start, char_end, tenant_id, workspace_id)
                VALUES
                    (:id, :source_id, 0, 1, :content, 0, :char_end, :tenant_id, :workspace_id)
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


class _FixedEmbedder:
    """An embedder that answers the same unit vector for every text.

    With one vector for the query and the stored chunk the ANN channel
    ranks the chunk first by construction; the test is about the
    database's row visibility, not about embedding quality.
    """

    class _Result:
        def __init__(self, embeddings: list[list[float]]) -> None:
            self.embeddings = embeddings

    async def embed(self, texts: list[str]) -> Any:
        return self._Result([list(UNIT_VECTOR) for _ in texts])


def _settings_for(database_url: str):
    from flycanon.config import CanonSettings

    return CanonSettings(
        database_url=database_url,
        vector_store="pgvector",
        embedding_dimensions=DIMENSION,
        pgvector_table="canon_chunk_vectors",
    )


async def _seed_vector_as_admin(admin_async_url: str, *, chunk_id: str, source_id: str, content: str) -> None:
    """Write the chunk's dense row through the production store, as the owner.

    This is what :class:`IndexService` does on ingest. It also proves the
    verify-first store on an existing table: 0016 created the table, so
    ``initialise`` must run no DDL here either (the admin could, but must
    not need to).
    """
    from flycanon.core.services.retrieval.corpus_factory import build_corpus_context

    context = build_corpus_context(settings=_settings_for(admin_async_url))
    try:
        await context.initialise()
        await context.vector_store.upsert(
            [
                VectorDocument(
                    id=chunk_id,
                    text=content,
                    embedding=list(UNIT_VECTOR),
                    metadata={"source_id": source_id, "doc_id": source_id, "section_path": "", "page": ""},
                )
            ],
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
        )
    finally:
        await context.close()


async def _search_as(app_async_url: str, *, query: str, tenant_id: str, workspace_id: str) -> list[Any]:
    """Run ``RetrievalService.search`` exactly as a request would, as *app_async_url*'s role."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from flycanon.core.services.embeddings.embedding_service import EmbeddingService
    from flycanon.core.services.retrieval.corpus_factory import build_corpus_context
    from flycanon.core.services.retrieval.retrieval_service import RetrievalService
    from flycanon.models.repositories.chunk_repository import ChunkRepository
    from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
    from flycanon.models.repositories.source_repository import SourceRepository
    from flycanon.web.conventions.context import TenantContext, set_tenant_context
    from flycanon.web.conventions.db import install_tenant_guc_hook

    install_tenant_guc_hook()
    engine = create_async_engine(app_async_url, future=True, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context = build_corpus_context(settings=_settings_for(app_async_url))
    service = RetrievalService(
        context=context,
        embeddings=EmbeddingService(embedder=_FixedEmbedder(), model="fixed", dimensions=DIMENSION),
        source_repository=SourceRepository(factory, engine=engine),
        chunk_repository=ChunkRepository(factory, engine=engine),
        knowledge_repository=KnowledgeRepository(factory, engine=engine),
        default_top_k=10,
        default_per_query_k=10,
        rrf_k=60,
    )
    # What TenantContextMiddleware does for a request: bind the scope so the
    # ORM sessions the hydration opens get their GUCs from after_begin.
    token = set_tenant_context(
        TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, correlation_id="search-under-app-role")
    )
    try:
        await context.initialise()  # as app_user: must be verify-only, no DDL
        result = await service.search(query=query, tenant_id=tenant_id, workspace_id=workspace_id)
        return result.hits
    finally:
        token.var.reset(token.token)
        await context.close()
        await engine.dispose()


def test_migration_0016_created_the_boot_tables_at_the_configured_width(
    pg_container,  # type: PostgresContainer
    pg_admin_engine: sa.Engine,
) -> None:
    """After ``alembic upgrade head`` the three tables exist; the vector column is ``vector(3)``."""
    with pg_admin_engine.connect() as conn:
        tables = set(
            conn.execute(
                sa.text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename IN "
                    "('canon_chunk_vectors', 'pyfly_eda_outbox', 'pyfly_eda_offsets')"
                )
            ).scalars()
        )
        assert tables == {"canon_chunk_vectors", "pyfly_eda_outbox", "pyfly_eda_offsets"}
        row = conn.execute(
            sa.text(
                """
                SELECT format_type(a.atttypid, a.atttypmod) AS column_type,
                       c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
                FROM pg_class c
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'embedding'
                WHERE c.relname = 'canon_chunk_vectors'
                """
            )
        ).one()
        assert row.column_type == f"vector({DIMENSION})"
        assert row.relrowsecurity and row.relforcerowsecurity and row.policies == 1


def test_fused_search_finds_the_chunk_as_the_app_role(
    pg_container,  # type: PostgresContainer
    pg_admin_engine: sa.Engine,
) -> None:
    """Seed as the owner, search as ``app_user``: one hit, hydrated with its source title."""
    source_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    content = "The daily meal allowance abroad is seventy euros; business class needs the CFO's approval."
    _seed_source_and_chunk(
        pg_admin_engine, source_id=source_id, chunk_id=chunk_id, content=content, title="Travel policy v3"
    )
    asyncio.run(
        _seed_vector_as_admin(
            _async_url(pg_container), chunk_id=chunk_id, source_id=source_id, content=content
        )
    )

    hits = asyncio.run(
        _search_as(
            _async_url(pg_container, user="app_user", password="app"),
            query="meal allowance abroad",
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
        )
    )
    assert [h.chunk_id for h in hits] == [chunk_id], (
        "the fused search returned nothing as app_user: either the BM25 corpus lost its GUC binding "
        "(PostgresCorpus._scoped_connection) or the dense store ran DDL it is not allowed to"
    )
    hit = hits[0]
    assert hit.source_id == source_id
    assert hit.content == content
    assert hit.metadata["source_title"] == "Travel policy v3"
    assert hit.metadata["source_filename"] == "Travel policy v3.txt"


def test_fused_search_stays_empty_for_a_foreign_scope_as_the_app_role(
    pg_container,  # type: PostgresContainer
    pg_admin_engine: sa.Engine,
) -> None:
    """The same words in another tenant's workspace: zero hits, on both channels, under RLS."""
    source_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    content = "Confidential: the acquisition closes on the first of the month."
    _seed_source_and_chunk(
        pg_admin_engine, source_id=source_id, chunk_id=chunk_id, content=content, title="Board minutes"
    )
    asyncio.run(
        _seed_vector_as_admin(
            _async_url(pg_container), chunk_id=chunk_id, source_id=source_id, content=content
        )
    )

    app_url = _async_url(pg_container, user="app_user", password="app")
    # Another tenant, same workspace slug.
    assert (
        asyncio.run(
            _search_as(app_url, query="acquisition closes", tenant_id="bcorp", workspace_id=WORKSPACE)
        )
        == []
    )
    # Same tenant, another workspace.
    assert (
        asyncio.run(
            _search_as(app_url, query="acquisition closes", tenant_id=TENANT, workspace_id="ws-other")
        )
        == []
    )
    # And the owner of the words still finds them -- first, because it is the
    # one chunk both channels agree on (the fixed embedder gives every chunk
    # of the workspace the same vector, so the ANN channel also returns the
    # previous test's chunk; RRF ranks the lexical+dense match above it).
    hits = asyncio.run(
        _search_as(app_url, query="acquisition closes", tenant_id=TENANT, workspace_id=WORKSPACE)
    )
    assert hits and hits[0].chunk_id == chunk_id
    with pg_admin_engine.connect() as conn:
        scopes = conn.execute(
            sa.text("SELECT DISTINCT tenant_id, workspace_id FROM canon_chunks WHERE id = ANY(:ids)"),
            {"ids": [h.chunk_id for h in hits]},
        ).all()
    assert scopes == [(TENANT, WORKSPACE)]


def test_app_role_holds_no_create_privilege(pg_container, pg_admin_engine: sa.Engine) -> None:  # type: ignore[no-untyped-def]
    """The control on the fixture: ``app_user`` could NOT have created anything above.

    If someone widens the conftest grants, the two search tests would pass
    for the wrong reason (the lazy DDL path succeeding); this pins the
    posture the tests are meant to prove.
    """
    with pg_admin_engine.connect() as conn:
        can_create = conn.execute(
            sa.text("SELECT has_schema_privilege('app_user', 'public', 'CREATE')")
        ).scalar_one()
        bypass = conn.execute(
            sa.text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_user'")
        ).scalar_one()
        owner = conn.execute(
            sa.text("SELECT tableowner FROM pg_tables WHERE tablename = 'canon_chunk_vectors'")
        ).scalar_one()
    assert can_create is False
    assert bypass is False
    assert owner != "app_user"
