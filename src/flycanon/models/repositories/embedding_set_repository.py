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

"""Async repository over ``canon_embedding_sets``.

Every read and write is bound to ``(tenant_id, workspace_id)`` in the
statement as well as by the RLS policy -- the policy is the boundary, the
predicate is the intent, and a test that runs under a BYPASSRLS role still
proves the intent.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.embedding_set import EmbeddingSetRow
from flycanon.models.entities.workspace import Workspace
from flycanon.models.repositories._engine import build_session_factory


class EmbeddingSetRepository:
    """Async repository over the ``canon_embedding_sets`` table."""

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
    def from_url(cls, database_url: str, *, echo: bool = False) -> EmbeddingSetRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def insert(self, row: EmbeddingSetRow) -> EmbeddingSetRow:
        async with self._session_factory() as session, session.begin():
            session.add(row)
        return row

    async def set_status(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        set_id: str,
        status: str,
        vector_count: int | None = None,
        chunk_count: int | None = None,
        index_name: str | None = None,
    ) -> None:
        """Move a set to ``status`` and stamp the matching timestamp.

        The timestamp is derived from the status rather than passed in, so a
        set can never be ``ready`` with no ``ready_at`` -- the pair is what an
        operator reads to tell a finished run from an abandoned one.
        """
        now = datetime.now(UTC)
        patch: dict[str, object] = {"status": status}
        if status == "ready":
            patch["ready_at"] = now
        elif status == "active":
            patch["activated_at"] = now
        elif status == "retired":
            patch["retired_at"] = now
        if vector_count is not None:
            patch["vector_count"] = vector_count
        if chunk_count is not None:
            patch["chunk_count"] = chunk_count
        if index_name is not None:
            patch["index_name"] = index_name
        async with self._session_factory() as session, session.begin():
            await session.execute(
                sa_update(EmbeddingSetRow)
                .where(
                    EmbeddingSetRow.id == set_id,
                    EmbeddingSetRow.tenant_id == tenant_id,
                    EmbeddingSetRow.workspace_id == workspace_id,
                )
                .values(**patch)
            )

    async def delete(self, *, tenant_id: str, workspace_id: str, set_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(EmbeddingSetRow, set_id)
            if row is not None and row.tenant_id == tenant_id and row.workspace_id == workspace_id:
                await session.delete(row)

    async def point_workspace_at(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        set_id: str | None,
    ) -> None:
        """The atomic switch: one UPDATE of one column, in one transaction.

        A query that started before it completes against the old set; a query
        after it against the new one. No request ever sees a mixture, because
        the set id is the search predicate and a transaction reads one value
        of it.
        """
        async with self._session_factory() as session, session.begin():
            await session.execute(
                sa_update(Workspace)
                .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
                .values(active_embedding_set_id=set_id)
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, *, tenant_id: str, workspace_id: str, set_id: str) -> EmbeddingSetRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(EmbeddingSetRow).where(
                    EmbeddingSetRow.id == set_id,
                    EmbeddingSetRow.tenant_id == tenant_id,
                    EmbeddingSetRow.workspace_id == workspace_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_for_workspace(self, *, tenant_id: str, workspace_id: str) -> list[EmbeddingSetRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(EmbeddingSetRow)
                .where(
                    EmbeddingSetRow.tenant_id == tenant_id,
                    EmbeddingSetRow.workspace_id == workspace_id,
                )
                .order_by(EmbeddingSetRow.created_at, EmbeddingSetRow.id)
            )
            return list(result.scalars())

    async def active_set_id(self, *, tenant_id: str, workspace_id: str) -> str | None:
        """The workspace's pointer. ``None`` means "use the process default"."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Workspace.active_embedding_set_id).where(
                    Workspace.id == workspace_id,
                    Workspace.tenant_id == tenant_id,
                )
            )
            return result.scalar_one_or_none()

    async def previous_active(
        self, *, tenant_id: str, workspace_id: str, exclude_set_id: str
    ) -> EmbeddingSetRow | None:
        """The most recently retired set -- what ``--rollback`` goes back to."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(EmbeddingSetRow)
                .where(
                    EmbeddingSetRow.tenant_id == tenant_id,
                    EmbeddingSetRow.workspace_id == workspace_id,
                    EmbeddingSetRow.id != exclude_set_id,
                    EmbeddingSetRow.status == "retired",
                )
                .order_by(EmbeddingSetRow.retired_at.desc(), EmbeddingSetRow.id.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
