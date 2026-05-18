# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for ``canon_cost_events`` + billing aggregates."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.repositories._engine import build_session_factory


class CostRepository:
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
    def from_url(cls, database_url: str, *, echo: bool = False) -> CostRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def add(self, row: CostEventRow) -> CostEventRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def list_events(
        self,
        *,
        actor: str | None = None,
        agent_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CostEventRow]:
        async with self._session_factory() as session:
            stmt = select(CostEventRow)
            if actor:
                stmt = stmt.where(CostEventRow.actor == actor)
            if agent_name:
                stmt = stmt.where(CostEventRow.agent_name == agent_name)
            if since is not None:
                stmt = stmt.where(CostEventRow.occurred_at >= since)
            if until is not None:
                stmt = stmt.where(CostEventRow.occurred_at <= until)
            stmt = stmt.order_by(CostEventRow.occurred_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def aggregate(
        self,
        *,
        group_by: Sequence[str],
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """Sum tokens + cost grouped by the named columns.

        ``group_by`` accepts ``model``, ``agent_name``, ``actor``,
        and ``date`` (occurred_at truncated to UTC day). Unknown
        groups are silently ignored so a typo doesn't return empty.
        """
        column_map = {
            "model": CostEventRow.model,
            "agent_name": CostEventRow.agent_name,
            "actor": CostEventRow.actor,
            "date": func.date(CostEventRow.occurred_at),
        }
        groups = [column_map[g] for g in group_by if g in column_map]
        if not groups:
            groups = [func.date(CostEventRow.occurred_at)]

        async with self._session_factory() as session:
            stmt = select(
                *groups,
                func.coalesce(func.sum(CostEventRow.input_tokens), 0),
                func.coalesce(func.sum(CostEventRow.output_tokens), 0),
                func.coalesce(func.sum(CostEventRow.total_tokens), 0),
                func.coalesce(func.sum(CostEventRow.cost_usd), 0),
                func.count(CostEventRow.id),
            )
            if actor:
                stmt = stmt.where(CostEventRow.actor == actor)
            if since is not None:
                stmt = stmt.where(CostEventRow.occurred_at >= since)
            if until is not None:
                stmt = stmt.where(CostEventRow.occurred_at <= until)
            stmt = stmt.group_by(*groups).order_by(*groups)
            rows = (await session.execute(stmt)).all()

        # Re-map rows back to a list of dicts keyed by the requested
        # groups + the aggregate columns. We zip the prefix slice of
        # length len(groups) so unknown names fall back to ``date``
        # without an off-by-one.
        group_names = [g for g in group_by if g in column_map] or ["date"]
        out: list[dict] = []
        for row in rows:
            head = list(row[: len(group_names)])
            tail = list(row[len(group_names) :])
            record: dict = {}
            for name, value in zip(group_names, head):
                record[name] = str(value) if value is not None else None
            record["input_tokens"] = int(tail[0] or 0)
            record["output_tokens"] = int(tail[1] or 0)
            record["total_tokens"] = int(tail[2] or 0)
            record["cost_usd"] = str(Decimal(tail[3] or 0))
            record["calls"] = int(tail[4] or 0)
            out.append(record)
        return out
