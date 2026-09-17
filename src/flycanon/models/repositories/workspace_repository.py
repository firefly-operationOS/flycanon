# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Async repository for ``canon_workspaces``.

The workspace surface is the entry point for multi-tenancy: every
other ``canon_*`` row hangs off a ``(tenant_id, workspace_id)`` pair
owned by a row in this table. The repository
mirrors :class:`flyradar.models.repositories.agent_token_repository.AgentTokenRepository`
in shape -- it returns plain ``dict`` rows so the service layer never
imports SQLAlchemy -- and follows the flycanon convention of taking a
shared ``session_factory`` + optional ``AsyncEngine`` (so the
container's actuator health probe can reach the engine through the
``engine`` property and the test fixtures can reuse the in-memory
SQLite engine).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.workspace import Workspace
from flycanon.models.repositories._engine import build_session_factory


class WorkspaceRepository:
    """Async repository over the ``canon_workspaces`` table."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
        admin_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        # Sessions on the BYPASSRLS engine for the one read that is
        # legitimately cross-workspace (``list_for_tenant``). ``None``
        # means "same engine as everything else", which is what every
        # test and single-role deployment gets.
        self._admin_session_factory = admin_session_factory or session_factory

    @property
    def engine(self) -> AsyncEngine | None:
        """Underlying ``AsyncEngine`` -- consumed by the actuator probe."""
        return self._engine

    @property
    def has_admin_engine(self) -> bool:
        """Whether ``list_for_tenant`` runs on a separate (admin) engine."""
        return self._admin_session_factory is not self._session_factory

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> WorkspaceRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @classmethod
    def from_urls(
        cls,
        database_url: str,
        *,
        admin_database_url: str,
        echo: bool = False,
    ) -> WorkspaceRepository:
        """Build with a distinct admin engine when the two DSNs differ.

        Identical DSNs collapse to one engine (``build_engine`` caches by
        URL anyway) so ``has_admin_engine`` stays ``False`` and the boot
        log can say so.
        """
        factory, engine = build_session_factory(database_url, echo=echo)
        if admin_database_url and admin_database_url != database_url:
            admin_factory, _admin_engine = build_session_factory(admin_database_url, echo=echo)
            return cls(factory, engine=engine, admin_session_factory=admin_factory)
        return cls(factory, engine=engine)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert(self, row: dict[str, Any]) -> None:
        """Persist a new workspace row.

        Callers pass a plain dict so the service layer never imports
        SQLAlchemy. ``tenant_id`` + ``id`` are required (the
        composite-key contract every ``canon_*`` table follows).
        """
        async with self._session_factory() as session, session.begin():
            session.add(Workspace(**row))

    async def update(
        self,
        tenant_id: str,
        workspace_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply a sparse patch to a workspace row.

        ``None`` values in ``patch`` are dropped so the call is a true
        sparse update -- a caller passing ``{"name": "x", "scope_json":
        None}`` updates only ``name`` and leaves ``scope_json``
        untouched. ``updated_at`` is bumped to ``NOW()`` whenever any
        column changes. Returns the post-update row, or ``None`` when
        the ``(tenant_id, workspace_id)`` pair does not exist.
        """
        clean = {k: v for k, v in patch.items() if v is not None}
        if not clean:
            # Nothing to write -- still honour the read-after-write
            # contract by returning the current row (or ``None`` when
            # the workspace does not exist).
            return await self.get(tenant_id, workspace_id)
        clean["updated_at"] = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            stmt = (
                sa_update(Workspace)
                .where(
                    Workspace.tenant_id == tenant_id,
                    Workspace.id == workspace_id,
                )
                .values(**clean)
            )
            result = await session.execute(stmt)
            rowcount = getattr(result, "rowcount", 0) or 0
            if rowcount == 0:
                return None
        return await self.get(tenant_id, workspace_id)

    async def close(self, tenant_id: str, workspace_id: str) -> bool:
        """Flip the workspace to ``closed`` and stamp ``closed_at``.

        Returns ``True`` when this call closed the row, ``False`` when
        no row matched the ``(tenant_id, workspace_id)`` pair. The
        method is intentionally idempotent at the row level (a second
        call just rewrites the same terminal state with a fresh
        ``closed_at``); the service layer is responsible for surfacing
        ``409 Conflict`` when re-closing should be rejected.
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            stmt = (
                sa_update(Workspace)
                .where(
                    Workspace.tenant_id == tenant_id,
                    Workspace.id == workspace_id,
                )
                .values(status="closed", closed_at=now, updated_at=now)
            )
            result = await session.execute(stmt)
            rowcount = getattr(result, "rowcount", 0) or 0
            return rowcount > 0

    async def close_if_open(self, tenant_id: str, workspace_id: str) -> bool:
        """Close the workspace only if it is not already ``closed``.

        Returns ``True`` only when THIS call performed the transition.
        This is the variant the purge uses: :meth:`close` rewrites
        ``closed_at`` / ``updated_at`` on every call and reports
        ``True`` each time, which made a repeated purge of an
        already-purged workspace answer ``closed: true``, republish
        ``WorkspaceDeleted`` and write a fresh ``workspace.purged`` audit
        row -- three side effects for an operation whose contract is
        "every counter is what this call erased". Guarding on
        ``status != 'closed'`` in the statement itself keeps it a single
        atomic UPDATE; two concurrent purges cannot both report the
        transition.
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            stmt = (
                sa_update(Workspace)
                .where(
                    Workspace.tenant_id == tenant_id,
                    Workspace.id == workspace_id,
                    Workspace.status != "closed",
                )
                .values(status="closed", closed_at=now, updated_at=now)
            )
            result = await session.execute(stmt)
            rowcount = getattr(result, "rowcount", 0) or 0
            return rowcount > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, tenant_id: str, workspace_id: str) -> dict[str, Any] | None:
        """Fetch a single workspace by composite key.

        Looking up by ``(tenant_id, workspace_id)`` -- rather than the
        bare primary key -- guards against the cross-tenant leak where
        tenant A guesses tenant B's workspace id. Returns ``None`` when
        the row does not exist or belongs to a different tenant.
        """
        async with self._session_factory() as session:
            stmt = select(Workspace).where(
                Workspace.tenant_id == tenant_id,
                Workspace.id == workspace_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_to_dict(row) if row else None

    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return every workspace owned by ``tenant_id``.

        Sorted by ``created_at`` descending so the most-recently-opened
        workspaces surface first in the admin list view.

        Runs on the ADMIN session factory. The ``canon_workspaces`` RLS
        policy is ``tenant_id = app.tenant_id AND id = app.workspace_id``
        (migration 0013), so on the request engine under the
        ``flycanon_app`` role this query could only ever return the one
        workspace named in the caller's ``X-Workspace-Id`` header --
        which is not a listing. The migration's own docstring promised
        "LIST is bypassed via BYPASSRLS for the workspace controller's
        admin path"; this is that path, wired through
        ``FLYCANON_ADMIN_DATABASE_URL``. The ``tenant_id`` WHERE clause
        remains the isolation boundary on the admin engine.
        """
        async with self._admin_session_factory() as session:
            stmt = (
                select(Workspace)
                .where(Workspace.tenant_id == tenant_id)
                .order_by(Workspace.created_at.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: Workspace) -> dict[str, Any]:
    """Materialise the ORM row into the plain dict the service layer uses."""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "status": row.status,
        "scope_json": row.scope_json,
        "sme_roster_json": row.sme_roster_json,
        "retention_days": row.retention_days,
        "jurisdiction": row.jurisdiction,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "closed_at": row.closed_at,
    }
