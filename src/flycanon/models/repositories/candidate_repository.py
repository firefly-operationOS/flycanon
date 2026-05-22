# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for :class:`CandidateRow`."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
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
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[list[CandidateRow], int]:
        conditions: list[Any] = []
        if statuses:
            conditions.append(CandidateRow.status.in_(list(statuses)))
        if source_id:
            conditions.append(CandidateRow.source_id == source_id)
        if domain:
            conditions.append(CandidateRow.domain == domain)
        if tenant_id:
            conditions.append(CandidateRow.tenant_id == tenant_id)
        if workspace_id:
            conditions.append(CandidateRow.workspace_id == workspace_id)

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

    async def find_conflict_candidate(
        self,
        *,
        from_item_id: str,
        to_item_id: str,
    ) -> CandidateRow | None:
        """Look up an already-queued conflict-detection candidate for the
        ordered pair ``(from_item_id, to_item_id)``.

        Used by :class:`ConflictDetector` to make the
        ``POST /api/v1/knowledge:detect-conflicts`` pass idempotent --
        re-running it shouldn't fill the inbox with duplicate
        proposals for the same pair. We anchor the SQL by
        ``source_id`` (the detector stores ``from_item.id`` there)
        and filter on the metadata.kind + to_item_id breadcrumb in
        Python so the helper stays portable across Postgres + SQLite
        without dialect-specific JSON operators.
        """
        async with self._session_factory() as session:
            stmt = (
                select(CandidateRow)
                .where(
                    CandidateRow.status == "proposed",
                    CandidateRow.source_id == from_item_id,
                )
                .order_by(CandidateRow.created_at.desc())
                .limit(50)
            )
            result = await session.execute(stmt)
            for row in result.scalars():
                meta = row.metadata_json or {}
                if meta.get("kind") != "conflict_detection":
                    continue
                if meta.get("to_item_id") != to_item_id:
                    continue
                return row
            return None

    async def update(self, row: CandidateRow) -> CandidateRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged

    async def claim_decision(
        self,
        candidate_id: str,
        *,
        new_status: str,
        actor: str | None,
    ) -> CandidateRow | None:
        """Atomically flip ``proposed -> new_status`` for one candidate.

        Used by :class:`CandidateService.accept` / ``reject`` to gate
        the decision against concurrent operators. Returns the claimed
        row when the caller wins the race; ``None`` when the candidate
        is no longer ``proposed`` (already accepted, rejected, or
        unknown id). The caller maps ``None`` to ``CandidateAlreadyDecided``
        / ``CandidateNotFound`` per the typed 4xx contract.

        Lower-cost than dropping a SELECT FOR UPDATE here because the
        check-then-act path lets two operators both pass the gate and
        both write a knowledge item before the second discovers the
        loss; the atomic flip catches it upfront.
        """
        async with self.session() as session:
            result = await session.execute(
                update(CandidateRow)
                .where(
                    CandidateRow.id == candidate_id,
                    CandidateRow.status == "proposed",
                )
                .values(
                    status=new_status,
                    decided_at=datetime.now(UTC),
                    decided_by=actor,
                )
                .returning(CandidateRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row

    async def finalise(
        self,
        candidate_id: str,
        *,
        materialised_knowledge_item_id: str | None = None,
        materialised_version: int | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> CandidateRow | None:
        """Attach the materialised pointer to an already-claimed candidate.

        Companion to :meth:`claim_decision`. ``accept`` first claims
        the candidate (which atomically pins ``status`` away from
        ``proposed``), runs the knowledge-item write, then calls this
        helper to record where the materialised content landed.
        """
        async with self.session() as session:
            values: dict[str, Any] = {}
            if materialised_knowledge_item_id is not None:
                values["materialised_knowledge_item_id"] = materialised_knowledge_item_id
            if materialised_version is not None:
                values["materialised_version"] = materialised_version
            if metadata_patch:
                row = await session.get(CandidateRow, candidate_id)
                if row is None:
                    return None
                merged_metadata = dict(row.metadata_json or {})
                merged_metadata.update(metadata_patch)
                values["metadata_json"] = merged_metadata
            if not values:
                return await session.get(CandidateRow, candidate_id)
            result = await session.execute(
                update(CandidateRow)
                .where(CandidateRow.id == candidate_id)
                .values(**values)
                .returning(CandidateRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row
