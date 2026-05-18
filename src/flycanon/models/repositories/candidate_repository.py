# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`CandidateRow`."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.repositories._engine import build_session_factory


class CandidateRepository:
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
    def from_url(cls, database_url: str, *, echo: bool = False) -> CandidateRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def get(self, candidate_id: str) -> CandidateRow | None:
        async with self._session_factory() as session:
            return await session.get(CandidateRow, candidate_id)

    async def list_candidates(
        self,
        *,
        statuses: Sequence[str] | None = None,
        source_id: str | None = None,
        domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CandidateRow], int]:
        conditions: list[Any] = []
        if statuses:
            conditions.append(CandidateRow.status.in_(list(statuses)))
        if source_id:
            conditions.append(CandidateRow.source_id == source_id)
        if domain:
            conditions.append(CandidateRow.domain == domain)

        async with self._session_factory() as session:
            stmt = select(CandidateRow)
            if conditions:
                stmt = stmt.where(*conditions)
            stmt = stmt.order_by(CandidateRow.created_at.desc()).limit(limit).offset(offset)
            rows = list((await session.execute(stmt)).scalars().all())

            count_stmt = select(func.count()).select_from(CandidateRow)
            if conditions:
                count_stmt = count_stmt.where(*conditions)
            total = int((await session.execute(count_stmt)).scalar() or 0)
            return rows, total

    async def add_many(self, rows: list[CandidateRow]) -> list[CandidateRow]:
        if not rows:
            return []
        async with self.session() as session:
            session.add_all(rows)
            await session.flush()
            for row in rows:
                await session.refresh(row)
            return rows

    async def update(self, row: CandidateRow) -> CandidateRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged
