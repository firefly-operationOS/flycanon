# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`TaxonomyNodeRow`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.taxonomy_node import TaxonomyNodeRow
from flycanon.models.repositories._engine import build_session_factory


class TaxonomyRepository:
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
    def from_url(cls, database_url: str, *, echo: bool = False) -> TaxonomyRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def get(self, node_id: str) -> TaxonomyNodeRow | None:
        async with self._session_factory() as session:
            return await session.get(TaxonomyNodeRow, node_id)

    async def list_all(self) -> list[TaxonomyNodeRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaxonomyNodeRow).order_by(TaxonomyNodeRow.depth.asc(), TaxonomyNodeRow.label.asc())
            )
            return list(result.scalars().all())

    async def add(self, row: TaxonomyNodeRow) -> TaxonomyNodeRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row
