# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`AuditEventRow`."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.audit_event import AuditEventRow
from flycanon.models.repositories._engine import build_session_factory


class AuditRepository:
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
    def from_url(cls, database_url: str, *, echo: bool = False) -> AuditRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def append(self, row: AuditEventRow) -> AuditEventRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def list_events(
        self,
        *,
        subject_id: str | None = None,
        subject_kind: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEventRow], int]:
        conditions: list[Any] = []
        if subject_id:
            conditions.append(AuditEventRow.subject_id == subject_id)
        if subject_kind:
            conditions.append(AuditEventRow.subject_kind == subject_kind)
        if event_type:
            conditions.append(AuditEventRow.event_type == event_type)

        async with self._session_factory() as session:
            stmt = select(AuditEventRow)
            if conditions:
                stmt = stmt.where(*conditions)
            stmt = stmt.order_by(AuditEventRow.occurred_at.desc()).limit(limit).offset(offset)
            rows = list((await session.execute(stmt)).scalars().all())

            count_stmt = select(func.count()).select_from(AuditEventRow)
            if conditions:
                count_stmt = count_stmt.where(*conditions)
            total = int((await session.execute(count_stmt)).scalar() or 0)
            return rows, total
