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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
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

    async def get(
        self,
        job_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> IngestJobRow | None:
        """Look up one ingest job, scoped to ``(tenant_id, workspace_id)``.

        Scope kwargs are MANDATORY. A job owned by a different
        workspace (same tenant) returns ``None`` instead of leaking to
        the caller.

        Workers that dispatch off the EDA payload (which carries the
        job id without the request context) must use
        :meth:`get_across_workspaces` -- the row's own scope columns
        provide the scope ex post, backed by a Postgres BYPASSRLS
        role.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(IngestJobRow).where(
                    IngestJobRow.id == job_id,
                    IngestJobRow.tenant_id == tenant_id,
                    IngestJobRow.workspace_id == workspace_id,
                )
            )
            return result.scalars().first()

    async def get_across_workspaces(self, job_id: str) -> IngestJobRow | None:
        """Cross-workspace lookup -- TRUSTED-CONTEXT callers only.

        Used by the EDA-driven ingest worker, which receives only
        the job id in the event payload and reads the row to learn
        its scope. The worker then threads the row's
        ``tenant_id`` / ``workspace_id`` through every subsequent
        repo call.
        """
        async with self._session_factory() as session:
            return await session.get(IngestJobRow, job_id)

    async def update(self, row: IngestJobRow) -> IngestJobRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged

    async def mark_running(
        self,
        job_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> IngestJobRow | None:
        """Atomic claim that also recovers stale ``running`` rows.

        Returns the claimed row on success, ``None`` when the job is
        already terminal, actively held by a live worker, or unknown.
        The single ``UPDATE ... WHERE ... RETURNING`` lets the database
        enforce mutual exclusion -- the previous read-modify-write
        pattern would let two replicas both succeed and double-ingest
        under duplicate EDA delivery.

        Stuck-job recovery: a row whose ``status='running'`` and whose
        ``started_at`` is older than ``lease_seconds`` is re-claimable.
        That handles the worker-crashed-mid-run case: without recovery
        a row sitting at ``running`` would be unreachable forever
        because the next worker's ``WHERE status='queued'`` would
        always miss it.

        Increments the ``attempts`` counter so a retry-on-failure loop
        has the correct count when classifying terminal vs retryable
        failures.
        """
        async with self.session() as session:
            conditions: list[Any] = [IngestJobRow.status == "queued"]
            if lease_seconds is not None and lease_seconds > 0:
                stale_cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
                conditions.append(
                    and_(
                        IngestJobRow.status == "running",
                        IngestJobRow.started_at.isnot(None),
                        IngestJobRow.started_at < stale_cutoff,
                    )
                )
            result = await session.execute(
                update(IngestJobRow)
                .where(IngestJobRow.id == job_id, or_(*conditions))
                .values(
                    status="running",
                    attempts=IngestJobRow.attempts + 1,
                    started_at=func.now(),
                    error_code=None,
                    error_message=None,
                )
                .returning(IngestJobRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row

    async def reclaim_stuck(
        self,
        *,
        lease_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """Bulk reset ``running`` rows whose lease has expired.

        Belt-and-braces companion to per-job lease recovery in
        :meth:`mark_running`. A long-stuck job whose EDA delivery has
        also been lost would never be re-claimed without an explicit
        sweep -- this method finds those rows, demotes them back to
        ``queued``, and returns the ids so the caller can republish
        the ``IngestSourceRequested`` event.
        """
        if lease_seconds <= 0:
            return []
        cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
        async with self.session() as session:
            stmt = (
                select(IngestJobRow.id)
                .where(
                    IngestJobRow.status == "running",
                    IngestJobRow.started_at.isnot(None),
                    IngestJobRow.started_at < cutoff,
                )
                .limit(limit)
            )
            ids = list((await session.execute(stmt)).scalars().all())
            if not ids:
                return []
            await session.execute(
                update(IngestJobRow)
                .where(IngestJobRow.id.in_(ids))
                .values(status="queued", error_code=None, error_message=None)
            )
            return ids

    async def mark_succeeded(
        self,
        job_id: str,
        *,
        source_id: str,
        content_sha256: str | None,
        expected_attempts: int | None = None,
    ) -> IngestJobRow | None:
        """Flip the job to ``succeeded`` -- atomically guarded by the
        original claim's lease.

        When ``expected_attempts`` is provided, the UPDATE only fires
        if the row's ``attempts`` still matches what the caller
        observed at claim time. This protects against the
        lease-poaching scenario: worker A claims, runs longer than
        ``ingest_timeout_s``, worker B re-claims (bumping attempts),
        worker A then tries to commit its result. Without the
        ``attempts`` guard worker A's success would silently
        overwrite worker B's state -- and worker B's later result
        publish would conflict with worker A's already-published one.
        With the guard, A's commit returns ``None`` and A logs +
        bows out.
        """
        async with self.session() as session:
            conditions: list[Any] = [
                IngestJobRow.id == job_id,
                IngestJobRow.status == "running",
            ]
            if expected_attempts is not None:
                conditions.append(IngestJobRow.attempts == expected_attempts)
            result = await session.execute(
                update(IngestJobRow)
                .where(*conditions)
                .values(
                    status="succeeded",
                    source_id=source_id,
                    content_sha256=content_sha256,
                    finished_at=func.now(),
                )
                .returning(IngestJobRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row

    async def mark_failed(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        expected_attempts: int | None = None,
    ) -> IngestJobRow | None:
        """Flip the job to ``failed`` -- mirrors the lease guard on
        :meth:`mark_succeeded`. See that docstring for the rationale."""
        async with self.session() as session:
            conditions: list[Any] = [
                IngestJobRow.id == job_id,
                IngestJobRow.status == "running",
            ]
            if expected_attempts is not None:
                conditions.append(IngestJobRow.attempts == expected_attempts)
            result = await session.execute(
                update(IngestJobRow)
                .where(*conditions)
                .values(
                    status="failed",
                    error_code=code,
                    error_message=message,
                    finished_at=func.now(),
                )
                .returning(IngestJobRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row

    async def list_jobs(
        self,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[IngestJobRow]:
        async with self._session_factory() as session:
            stmt = select(IngestJobRow)
            if statuses:
                stmt = stmt.where(IngestJobRow.status.in_(list(statuses)))
            if tenant_id:
                stmt = stmt.where(IngestJobRow.tenant_id == tenant_id)
            if workspace_id:
                stmt = stmt.where(IngestJobRow.workspace_id == workspace_id)
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
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> IngestJobEventRow:
        """Append one progress event for a job.

        ``tenant_id`` + ``workspace_id`` default to ``"default"``;
        every production call site threads the parent job's scope so
        the per-event projection lines up with the parent row.
        """
        async with self.session() as session:
            event = IngestJobEventRow(
                tenant_id=tenant_id or "default",
                workspace_id=workspace_id or "default",
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
        tenant_id: str,
        workspace_id: str,
        after_id: int | None = None,
        limit: int = 200,
    ) -> list[IngestJobEventRow]:
        """Return events for a job ordered by id ascending.

        Scope kwargs are MANDATORY. Events tied to a job owned by a
        different workspace (same tenant) return an empty list instead
        of leaking.

        The SSE endpoint passes ``after_id`` so a long-running poll
        only fetches the deltas since its last cursor position.
        """
        async with self._session_factory() as session:
            stmt = select(IngestJobEventRow).where(
                IngestJobEventRow.job_id == job_id,
                IngestJobEventRow.tenant_id == tenant_id,
                IngestJobEventRow.workspace_id == workspace_id,
            )
            if after_id is not None:
                stmt = stmt.where(IngestJobEventRow.id > after_id)
            stmt = stmt.order_by(IngestJobEventRow.id.asc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())
