# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`SourceRow`."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories._engine import build_session_factory


class SourceRepository:
    """Async repository for ``canon_sources``."""

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
        """Underlying ``AsyncEngine``. Used by the actuator health probe."""
        return self._engine

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> SourceRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        """Open a session for callers that need to compose multiple operations."""
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def get(
        self,
        source_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> SourceRow | None:
        """Look up one source, scoped to ``(tenant_id, workspace_id)``.

        Plan 6 Task 1 closes the workspace-scope gap: the scope kwargs
        are MANDATORY. A row living in a different workspace (same
        tenant) returns ``None`` instead of leaking to the caller.

        Worker code that walks the table without a request scope must
        use :meth:`get_across_workspaces` explicitly.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(SourceRow).where(
                    SourceRow.id == source_id,
                    SourceRow.tenant_id == tenant_id,
                    SourceRow.workspace_id == workspace_id,
                )
            )
            return result.scalars().first()

    async def get_across_workspaces(self, source_id: str) -> SourceRow | None:
        """Cross-workspace lookup -- TRUSTED-CONTEXT callers only.

        Used by workers / sweepers that operate without a request
        scope (the row's own scope columns provide it ex post). Plan
        6 Task 4 replaces this code-level escape hatch with a
        Postgres BYPASSRLS role.

        Do NOT call from request-scoped code paths.
        """
        async with self._session_factory() as session:
            return await session.get(SourceRow, source_id)

    async def get_many(self, source_ids: Sequence[str]) -> list[SourceRow]:
        """Batch lookup -- used by the retrieval hydration step to
        enrich every chunk hit with its source filename / title /
        kind without N+1 round-trips.
        """
        ids = list(source_ids)
        if not ids:
            return []
        async with self._session_factory() as session:
            result = await session.execute(select(SourceRow).where(SourceRow.id.in_(ids)))
            return list(result.scalars().all())

    async def get_by_content_sha256(self, content_sha256: str) -> SourceRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SourceRow).where(SourceRow.content_sha256 == content_sha256)
            )
            return result.scalars().first()

    async def list_sources(
        self,
        *,
        statuses: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[list[SourceRow], int]:
        conditions: list[Any] = []
        if statuses:
            conditions.append(SourceRow.status.in_(list(statuses)))
        if kinds:
            conditions.append(SourceRow.kind.in_(list(kinds)))
        if tenant_id:
            conditions.append(SourceRow.tenant_id == tenant_id)
        if workspace_id:
            conditions.append(SourceRow.workspace_id == workspace_id)

        async with self._session_factory() as session:
            stmt = select(SourceRow)
            if conditions:
                stmt = stmt.where(*conditions)
            stmt = stmt.order_by(SourceRow.created_at.desc()).limit(limit).offset(offset)
            rows = list((await session.execute(stmt)).scalars().all())

            count_stmt = select(func.count()).select_from(SourceRow)
            if conditions:
                count_stmt = count_stmt.where(*conditions)
            total = int((await session.execute(count_stmt)).scalar() or 0)
            return rows, total

    async def add(self, row: SourceRow) -> SourceRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def update(self, row: SourceRow) -> SourceRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged
