# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`KnowledgeRelationRow`."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow
from flycanon.models.repositories._engine import build_session_factory


class RelationRepository:
    """Owns ``canon_knowledge_relations``."""

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
    def from_url(cls, database_url: str, *, echo: bool = False) -> RelationRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def add(self, row: KnowledgeRelationRow) -> KnowledgeRelationRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def get(self, relation_id: str) -> KnowledgeRelationRow | None:
        async with self._session_factory() as session:
            return await session.get(KnowledgeRelationRow, relation_id)

    async def delete(self, relation_id: str) -> bool:
        """Drop one relation by id. Returns ``True`` when a row was deleted."""
        async with self.session() as session:
            result = await session.execute(
                delete(KnowledgeRelationRow).where(KnowledgeRelationRow.id == relation_id)
            )
            return (result.rowcount or 0) > 0

    async def list_for_item(
        self,
        item_id: str,
        *,
        direction: str = "both",
    ) -> list[KnowledgeRelationRow]:
        """Return relations touching ``item_id``.

        ``direction``:
            - ``out``  -- relations where ``from_item_id == item_id``
            - ``in``   -- relations where ``to_item_id == item_id``
            - ``both`` (default) -- both sides
        """
        async with self._session_factory() as session:
            if direction == "out":
                cond = KnowledgeRelationRow.from_item_id == item_id
            elif direction == "in":
                cond = KnowledgeRelationRow.to_item_id == item_id
            else:
                cond = or_(
                    KnowledgeRelationRow.from_item_id == item_id,
                    KnowledgeRelationRow.to_item_id == item_id,
                )
            result = await session.execute(
                select(KnowledgeRelationRow).where(cond).order_by(KnowledgeRelationRow.created_at.asc())
            )
            return list(result.scalars().all())

    async def list_all(
        self,
        *,
        from_item_ids: Sequence[str] | None = None,
        to_item_ids: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
    ) -> list[KnowledgeRelationRow]:
        """Bulk listing used by the graph-viz endpoint."""
        async with self._session_factory() as session:
            stmt = select(KnowledgeRelationRow)
            if from_item_ids:
                stmt = stmt.where(KnowledgeRelationRow.from_item_id.in_(list(from_item_ids)))
            if to_item_ids:
                stmt = stmt.where(KnowledgeRelationRow.to_item_id.in_(list(to_item_ids)))
            if kinds:
                stmt = stmt.where(KnowledgeRelationRow.kind.in_(list(kinds)))
            result = await session.execute(stmt.order_by(KnowledgeRelationRow.created_at.asc()))
            return list(result.scalars().all())
