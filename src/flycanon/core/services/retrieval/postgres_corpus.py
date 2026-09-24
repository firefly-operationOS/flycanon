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

"""``PostgresCorpus`` -- BM25 over ``canon_chunks`` via tsvector + GIN.

Implements the corpus surface that :class:`HybridRetriever` consumes
on top of the canonical Postgres ``canon_chunks`` table -- the same
table the rest of flycanon writes via :class:`ChunkRepository`. The
BM25 projection rides on a GENERATED ``tsv`` column populated
automatically by Postgres (see migration ``0003_bm25_tsv``); flycanon
never has to maintain it.

Methods called by the retriever:

* :meth:`bm25_search`  -- returns the top-k chunks for a free-text
  query, ranked by ``ts_rank_cd``.
* :meth:`get_chunks`   -- hydrates a list of chunk ids into the
  ``StoredChunk`` shape the retriever's RRF pass expects.

Methods used by :class:`IndexService` to keep the corpus interface
stable:

* :meth:`upsert_chunks`     -- no-op (canon_chunks is the canonical
  store; the ``tsv`` projection is maintained by the GENERATED
  column).
* :meth:`delete_by_doc_id`  -- no-op (the source-id FK cascades the
  delete to ``canon_chunks``).
* :meth:`initialise` / :meth:`close` -- no-ops (Alembic owns the
  schema lifecycle).

This module never reads or writes ``canon_chunk_vectors`` -- the
dense projection lives on :class:`PgVectorVectorStore`.

Row-level security
------------------

``canon_chunks`` and ``canon_sources`` are FORCE-RLS tables (migration
``0013``): a role without ``BYPASSRLS`` sees only the rows whose
``(tenant_id, workspace_id)`` equal the ``app.tenant_id`` /
``app.workspace_id`` GUCs of the current transaction, and sees NOTHING
when they are unset. The ORM path sets them through the ``after_begin``
listener in :mod:`flycanon.web.conventions.db`; this corpus, however,
runs its reads on a bare Core connection of its own engine, where no
Session -- and so no listener -- is involved. Until 26.7.1 that meant
the BM25 channel returned zero rows under the application role, and
the fused ``/search`` answered ``hits: []`` while the vector channel
alone found the chunk (measured by the dworkers programme on
2026-09-17 against a ``flycanon_app`` role; invisible in a stack where
every process is the database owner). Every scoped read here therefore
binds the GUCs itself, from the explicit scope it is handed, inside the
same transaction as the query -- see :meth:`_scoped_connection`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from flycanon.core.services.retrieval.fusion import ChunkHit, StoredChunk

logger = logging.getLogger(__name__)


class PostgresCorpus:
    """BM25 corpus surface on ``canon_chunks``."""

    def __init__(self, database_url: str, *, search_config: str = "simple") -> None:
        # Reuse the async engine pattern from the rest of flycanon --
        # one engine per repository / corpus, pooled by asyncpg
        # itself.
        self._database_url = database_url
        self._search_config = search_config
        self._engine: AsyncEngine | None = None
        self._lock = asyncio.Lock()

    async def _ensure_engine(self) -> AsyncEngine:
        if self._engine is None:
            async with self._lock:
                if self._engine is None:
                    self._engine = create_async_engine(self._database_url, pool_pre_ping=True)
        return self._engine

    @asynccontextmanager
    async def _scoped_connection(
        self, *, tenant_id: str, workspace_id: str
    ) -> AsyncIterator[AsyncConnection]:
        """Open a transaction with the RLS GUCs bound to *tenant_id* / *workspace_id*.

        ``SET LOCAL`` cannot take bind parameters, so the values go through
        ``set_config(name, value, is_local => true)``, which can -- no SQL
        literal is ever assembled from a caller-supplied slug, whatever the
        validator upstream did. ``is_local`` ties the setting to the
        transaction ``engine.begin()`` opened, so it rolls off at commit and
        never leaks into the next checkout of the pooled connection (the
        ``after_begin`` listener relies on the same property).

        The scope is the explicit ``(tenant_id, workspace_id)`` the read was
        handed, not the request ContextVar: :class:`_ScopedCorpus` binds the
        request scope onto every call already, the same pair the SQL
        ``WHERE`` clause filters on, so the policy and the predicate can never
        disagree, and a caller outside a request (a test, an admin tool)
        gets exactly the scope it named.

        On a non-Postgres dialect (SQLite in unit tests) nothing is set: the
        GUCs are a Postgres concept and the ``WHERE`` clause alone scopes the
        read, as before.
        """
        engine = await self._ensure_engine()
        async with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )
                await conn.execute(
                    text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                    {"workspace_id": workspace_id},
                )
            yield conn

    # ------------------------------------------------------------------
    # Lifecycle (no-ops -- the schema is owned by Alembic)
    # ------------------------------------------------------------------

    async def initialise(self) -> None:
        await self._ensure_engine()

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # Writes (no-ops -- ChunkRepository owns canon_chunks; the tsv
    # column is GENERATED so the BM25 projection follows automatically).
    # ------------------------------------------------------------------

    async def upsert_chunks(self, chunks: Any) -> None:  # noqa: ARG002
        return None

    async def delete_by_doc_id(self, doc_id: str) -> int:  # noqa: ARG002
        # ``canon_chunks.source_id`` has ON DELETE CASCADE on the FK
        # to ``canon_sources``; per-source clean-up happens via
        # :class:`ChunkRepository.replace_for_source`.
        return 0

    async def clear_all(self) -> None:
        """Delete every chunk of every scope -- an operator's reset, not a request path.

        Deliberately unscoped and without GUCs: under a ``NOBYPASSRLS``
        role the FORCE-RLS policy makes this a no-op (zero rows match an
        unset GUC), which is the right outcome -- only the BYPASSRLS
        maintenance role can wipe the corpus, and it does so on purpose.
        """
        engine = await self._ensure_engine()
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM canon_chunks"))

    # ------------------------------------------------------------------
    # Reads -- the surface :class:`HybridRetriever` consumes
    # ------------------------------------------------------------------

    async def bm25_search(
        self,
        query: str,
        *,
        top_k: int = 30,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> list[ChunkHit]:
        """Return the top-``top_k`` chunks ranked by Postgres BM25.

        Uses ``plainto_tsquery`` for the user query (safe vs. raw
        text) and ``ts_rank_cd`` for the score. ``ts_rank_cd``
        boosts term density better than ``ts_rank`` -- closer to
        traditional BM25 semantics. Hits with empty/whitespace
        queries return ``[]`` rather than scanning the whole table.

        ``tenant_id`` / ``workspace_id`` are the authoritative scope
        -- the BM25 query filters the chunk projection on the
        ``(tenant_id, workspace_id)`` composite BEFORE the
        ``tsvector @@ plainto_tsquery`` match so a foreign-scope
        chunk can never bleed into another workspace's hit list. The
        composite is selective enough that the GIN-on-tsv index still
        gets picked for the match. The same pair is bound as the RLS
        GUCs for the transaction (:meth:`_scoped_connection`), so the
        query returns rows under a ``NOBYPASSRLS`` role too.
        """
        q = (query or "").strip()
        if not q:
            return []
        sql = text(
            """
            SELECT
                c.id          AS chunk_id,
                c.source_id   AS doc_id,
                c.content     AS content,
                c.section_path,
                c.page,
                c.metadata_json,
                COALESCE(s.filename, s.uri, s.id) AS source_path,
                ts_rank_cd(c.tsv, plainto_tsquery(:cfg, :q)) AS score
            FROM canon_chunks AS c
            JOIN canon_sources AS s ON s.id = c.source_id
            WHERE c.tenant_id = :tenant_id
              AND c.workspace_id = :workspace_id
              AND c.tsv @@ plainto_tsquery(:cfg, :q)
            ORDER BY score DESC, c.created_at DESC
            LIMIT :k
            """
        )
        async with self._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as conn:
            result = await conn.execute(
                sql,
                {
                    "cfg": self._search_config,
                    "q": q,
                    "k": int(top_k),
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                },
            )
            rows = result.mappings().all()

        hits: list[ChunkHit] = []
        for row in rows:
            metadata: dict[str, Any] = dict(row["metadata_json"] or {})
            if row["section_path"]:
                metadata.setdefault("section_path", str(row["section_path"]))
            if row["page"] is not None:
                # Stored as string so the downstream
                # ``ChunkHit.metadata`` shape (dict[str, str]) stays
                # honest -- callers cast back to int when they need
                # the numeric value (the DTO mapper does this).
                metadata.setdefault("page", str(row["page"]))
            hits.append(
                ChunkHit(
                    chunk_id=row["chunk_id"],
                    score=float(row["score"] or 0.0),
                    content=row["content"] or "",
                    metadata=metadata,
                    source_path=row["source_path"] or "",
                    doc_id=row["doc_id"] or "",
                )
            )
        return hits

    async def get_chunks(
        self,
        chunk_ids: list[str],
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> list[StoredChunk]:
        """Hydrate a list of chunk ids into ``StoredChunk`` rows.

        The retriever uses this after RRF fusion to fetch the body
        text + provenance for the chosen ids. Order is preserved to
        match the input list so the fusion ranks line up with the
        hydrated rows.

        ``tenant_id`` / ``workspace_id`` are filtered on the SQL side
        so a caller that presents a foreign chunk id (whether by bug
        or by malice) cannot read content from a workspace they don't
        own -- and bound as the RLS GUCs of the transaction, so the
        hydration works under a ``NOBYPASSRLS`` role.
        """
        if not chunk_ids:
            return []
        sql = text(
            """
            SELECT
                c.id          AS chunk_id,
                c.source_id   AS doc_id,
                c.content     AS content,
                c.index_in_source,
                c.section_path,
                c.page,
                c.metadata_json,
                COALESCE(s.filename, s.uri, s.id) AS source_path
            FROM canon_chunks AS c
            JOIN canon_sources AS s ON s.id = c.source_id
            WHERE c.tenant_id = :tenant_id
              AND c.workspace_id = :workspace_id
              AND c.id = ANY(:ids)
            """
        )
        async with self._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as conn:
            result = await conn.execute(
                sql,
                {
                    "ids": list(chunk_ids),
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                },
            )
            rows = {row["chunk_id"]: row for row in result.mappings().all()}

        ordered: list[StoredChunk] = []
        for chunk_id in chunk_ids:
            row = rows.get(chunk_id)
            if row is None:
                continue
            metadata: dict[str, Any] = dict(row["metadata_json"] or {})
            if row["section_path"]:
                metadata.setdefault("section_path", row["section_path"])
            if row["page"] is not None:
                metadata.setdefault("page", row["page"])
            ordered.append(
                StoredChunk(
                    chunk_id=row["chunk_id"],
                    doc_id=row["doc_id"],
                    source_path=row["source_path"] or "",
                    index_in_doc=int(row["index_in_source"] or 0),
                    content=row["content"] or "",
                    metadata=metadata,
                )
            )
        return ordered
