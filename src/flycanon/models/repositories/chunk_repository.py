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

"""Async repository for :class:`KnowledgeChunkRow`."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import delete, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.repositories._engine import build_session_factory


class ChunkRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> ChunkRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def list_for_source(self, source_id: str) -> list[KnowledgeChunkRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeChunkRow)
                .where(KnowledgeChunkRow.source_id == source_id)
                .order_by(KnowledgeChunkRow.index_in_source.asc())
            )
            return list(result.scalars().all())

    async def get_many(self, chunk_ids: Sequence[str]) -> list[KnowledgeChunkRow]:
        if not chunk_ids:
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeChunkRow).where(KnowledgeChunkRow.id.in_(list(chunk_ids)))
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # The reindex reads -- keyset-paged over a whole workspace
    # ------------------------------------------------------------------

    async def list_for_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        after_id: str | None = None,
        limit: int = 256,
    ) -> list[KnowledgeChunkRow]:
        """One batch of a workspace's chunks, ordered by id, after ``after_id``.

        Keyset pagination rather than OFFSET: a re-embed of a large workspace
        walks the whole table and an OFFSET scan would re-read everything it
        has already passed on every batch. The id order is arbitrary but
        stable, which is all a resume cursor needs.
        """
        async with self._session_factory() as session:
            statement = (
                select(KnowledgeChunkRow)
                .where(
                    KnowledgeChunkRow.tenant_id == tenant_id,
                    KnowledgeChunkRow.workspace_id == workspace_id,
                )
                .order_by(KnowledgeChunkRow.id.asc())
                .limit(limit)
            )
            if after_id is not None:
                statement = statement.where(KnowledgeChunkRow.id > after_id)
            result = await session.execute(statement)
            return list(result.scalars().all())

    async def count_for_workspace(self, *, tenant_id: str, workspace_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(KnowledgeChunkRow)
                .where(
                    KnowledgeChunkRow.tenant_id == tenant_id,
                    KnowledgeChunkRow.workspace_id == workspace_id,
                )
            )
            return int(result.scalar_one() or 0)

    async def input_chars_for_workspace(
        self, *, tenant_id: str, workspace_id: str, max_input_chars: int
    ) -> int:
        """Total characters the embedder would actually see for this workspace.

        ``LEAST(length(content), max_input_chars)`` because
        :attr:`EmbeddingService._MAX_INPUT_CHARS` truncates every input before
        it is sent -- an estimate that ignores the truncation overstates the
        bill, sometimes by a lot on a corpus with a few very long chunks.
        """
        async with self._session_factory() as session:
            # Postgres spells the two-argument minimum LEAST (``min`` there is
            # the aggregate); SQLite spells it ``min``. The unit tests run on
            # SQLite and the reindex runs on Postgres, so both are wired.
            scalar_min = func.least if session.get_bind().dialect.name == "postgresql" else func.min
            capped = scalar_min(func.length(KnowledgeChunkRow.content), max_input_chars)
            result = await session.execute(
                select(func.coalesce(func.sum(capped), 0)).where(
                    KnowledgeChunkRow.tenant_id == tenant_id,
                    KnowledgeChunkRow.workspace_id == workspace_id,
                )
            )
            return int(result.scalar_one() or 0)

    async def scopes_with_chunks(self, *, tenant_id: str | None = None) -> list[tuple[str, str]]:
        """Every ``(tenant_id, workspace_id)`` that holds chunks.

        A cross-workspace read: under a NOBYPASSRLS role the policy collapses
        it to the scope in the GUCs, which is why ``flycanon reindex`` runs it
        on the admin engine and says so when that engine is the request one.
        """
        async with self._session_factory() as session:
            statement = select(KnowledgeChunkRow.tenant_id, KnowledgeChunkRow.workspace_id).distinct()
            if tenant_id is not None:
                statement = statement.where(KnowledgeChunkRow.tenant_id == tenant_id)
            result = await session.execute(statement.order_by(KnowledgeChunkRow.tenant_id))
            return [(str(t), str(w)) for t, w in result.all()]

    async def stamp_embedding_model(self, *, chunk_ids: Sequence[str], embedding_model: str) -> int:
        """Record which embedder produced the vectors for ``chunk_ids``.

        Written in the same unit of work as the vectors themselves, because a
        resume cursor that disagrees with the rows it is pointing at is worse
        than no cursor.
        """
        if not chunk_ids:
            return 0
        async with self._session_factory() as session, session.begin():
            await session.execute(
                sa_update(KnowledgeChunkRow)
                .where(KnowledgeChunkRow.id.in_(list(chunk_ids)))
                .values(embedding_model=embedding_model)
            )
            return len(list(chunk_ids))

    async def replace_for_source(
        self,
        source_id: str,
        rows: Iterable[KnowledgeChunkRow],
    ) -> int:
        """Replace every chunk for ``source_id`` with ``rows`` atomically.

        The unit-of-work: delete every existing chunk for the source,
        then bulk-insert the new set. Returns the number of inserted
        rows.
        """
        prepared = list(rows)
        async with self._session_factory() as session:
            await session.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.source_id == source_id))
            if prepared:
                session.add_all(prepared)
                await session.flush()
            await session.commit()
            return len(prepared)
