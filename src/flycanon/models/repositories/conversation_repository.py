# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for ``canon_conversations`` + their turns."""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.conversation import ConversationRow, ConversationTurnRow
from flycanon.models.repositories._engine import build_session_factory


class ConversationRepository:
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
    def from_url(cls, database_url: str, *, echo: bool = False) -> ConversationRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def add(self, row: ConversationRow) -> ConversationRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def get(
        self,
        conversation_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> ConversationRow | None:
        """Look up one conversation, scoped to ``(tenant_id, workspace_id)``.

        Plan 6 Task 1: scope kwargs are MANDATORY. A conversation
        living in a different workspace (same tenant) returns
        ``None`` instead of leaking.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id,
                    ConversationRow.tenant_id == tenant_id,
                    ConversationRow.workspace_id == workspace_id,
                )
            )
            return result.scalars().first()

    async def update(self, row: ConversationRow) -> ConversationRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    async def add_turn(self, row: ConversationTurnRow) -> ConversationTurnRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def list_turns(
        self,
        conversation_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> list[ConversationTurnRow]:
        """Return turns for ``conversation_id``, scoped to ``(tenant, workspace)``.

        Scope kwargs are MANDATORY -- Plan 6 Task 1. Turns belonging
        to a conversation owned by a different workspace (same
        tenant) return an empty list.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ConversationTurnRow)
                .where(
                    ConversationTurnRow.conversation_id == conversation_id,
                    ConversationTurnRow.tenant_id == tenant_id,
                    ConversationTurnRow.workspace_id == workspace_id,
                )
                .order_by(ConversationTurnRow.turn_index.asc())
            )
            return list(result.scalars().all())

    async def next_turn_index(self, conversation_id: str) -> int:
        async with self._session_factory() as session:
            current = await session.execute(
                select(func.max(ConversationTurnRow.turn_index)).where(
                    ConversationTurnRow.conversation_id == conversation_id
                )
            )
            value = current.scalar()
            return (int(value) + 1) if value is not None else 1
