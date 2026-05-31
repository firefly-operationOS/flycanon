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

from sqlalchemy import delete, select
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
