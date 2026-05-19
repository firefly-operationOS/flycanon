# Copyright 2026 Firefly Software Solutions Inc
"""Async-ingest job repository.

Two tables, one repository:

* ``canon_ingest_jobs`` -- the job-tracking row updated as the
  worker walks the lifecycle (queued -> running -> succeeded /
  failed).
* ``canon_ingest_job_events`` -- append-only progress trail the
  SSE streaming endpoint replays to the caller. We expose
  :meth:`append_event` so the worker can emit one event per
  pipeline stage without re-fetching the parent row.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.ingest_job import IngestJobEventRow, IngestJobRow
from flycanon.models.repositories._engine import build_session_factory


class IngestJobRepository:
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
    def from_url(cls, database_url: str, *, echo: bool = False) -> IngestJobRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    async def add(self, row: IngestJobRow) -> IngestJobRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def get(self, job_id: str) -> IngestJobRow | None:
        async with self._session_factory() as session:
            return await session.get(IngestJobRow, job_id)

    async def update(self, row: IngestJobRow) -> IngestJobRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged

    async def mark_running(self, job_id: str) -> IngestJobRow | None:
        """Atomic ``queued -> running`` claim.

        Returns the claimed row on success, ``None`` if another worker
        beat us to it (the row already left ``queued``) or the id is
        unknown. The single ``UPDATE ... WHERE status = 'queued'
        RETURNING`` lets the database enforce mutual exclusion -- the
        previous read-modify-write pattern would let two replicas both
        succeed and double-ingest under duplicate EDA delivery.

        Increments the ``attempts`` counter so a retry-on-failure loop
        has the correct count when classifying terminal vs retryable
        failures.
        """
        async with self.session() as session:
            result = await session.execute(
                update(IngestJobRow)
                .where(IngestJobRow.id == job_id, IngestJobRow.status == "queued")
                .values(
                    status="running",
                    attempts=IngestJobRow.attempts + 1,
                    started_at=func.coalesce(IngestJobRow.started_at, func.now()),
                    error_code=None,
                    error_message=None,
                )
                .returning(IngestJobRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        source_id: str,
        content_sha256: str | None,
    ) -> IngestJobRow | None:
        async with self.session() as session:
            row = await session.get(IngestJobRow, job_id)
            if row is None:
                return None
            row.status = "succeeded"
            row.source_id = source_id
            row.content_sha256 = content_sha256
            row.finished_at = datetime.now(UTC)
            await session.flush()
            return row

    async def mark_failed(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
    ) -> IngestJobRow | None:
        async with self.session() as session:
            row = await session.get(IngestJobRow, job_id)
            if row is None:
                return None
            row.status = "failed"
            row.error_code = code
            row.error_message = message
            row.finished_at = datetime.now(UTC)
            await session.flush()
            return row

    async def list_jobs(
        self,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[IngestJobRow]:
        async with self._session_factory() as session:
            stmt = select(IngestJobRow)
            if statuses:
                stmt = stmt.where(IngestJobRow.status.in_(list(statuses)))
            stmt = stmt.order_by(IngestJobRow.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def append_event(
        self,
        job_id: str,
        *,
        stage: str,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> IngestJobEventRow:
        async with self.session() as session:
            event = IngestJobEventRow(
                job_id=job_id,
                stage=stage,
                message=message,
                payload_json=dict(payload or {}),
            )
            session.add(event)
            await session.flush()
            await session.refresh(event)
            return event

    async def list_events(
        self,
        job_id: str,
        *,
        after_id: int | None = None,
        limit: int = 200,
    ) -> list[IngestJobEventRow]:
        """Return events for a job ordered by id ascending.

        The SSE endpoint passes ``after_id`` so a long-running poll
        only fetches the deltas since its last cursor position.
        """
        async with self._session_factory() as session:
            stmt = select(IngestJobEventRow).where(IngestJobEventRow.job_id == job_id)
            if after_id is not None:
                stmt = stmt.where(IngestJobEventRow.id > after_id)
            stmt = stmt.order_by(IngestJobEventRow.id.asc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())
