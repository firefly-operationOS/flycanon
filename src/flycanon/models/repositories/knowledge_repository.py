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

"""Async repository for knowledge items, versions, and citation rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.repositories._engine import build_session_factory


@dataclass(frozen=True, slots=True)
class KnowledgeLink:
    """Snapshot of a knowledge_version's hit-time dimensions.

    Returned by :meth:`KnowledgeRepository.lookup_published_citations_for_chunks`
    so retrieval hit hydration can populate ``Hit.knowledge_item_id`` /
    ``Hit.knowledge_version`` and the retrieval post-filter can match
    against status / domain / jurisdiction / tags without a second
    repo round-trip per hit.
    """

    item_id: str
    version: int
    status: str
    domain: str
    jurisdiction: str
    tags: tuple[str, ...] = field(default_factory=tuple)


class KnowledgeRepository:
    """Owns ``canon_knowledge_items``, ``canon_knowledge_versions`` and
    ``canon_citations`` -- they always travel together."""

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
    def from_url(cls, database_url: str, *, echo: bool = False) -> KnowledgeRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    @asynccontextmanager
    async def session(self):
        async with self._session_factory() as session:
            yield session
            await session.commit()

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    async def get_item(
        self,
        item_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> KnowledgeItemRow | None:
        """Look up one knowledge item, scoped to ``(tenant_id, workspace_id)``.

        The scope kwargs are MANDATORY on every read-by-id path. A row
        that sits in a different workspace (same tenant) returns
        ``None`` instead of leaking across.

        Workers + retention sweepers that legitimately need to scan
        across workspaces must use :meth:`get_item_across_workspaces`
        explicitly -- that method is the documented escape hatch
        backed by a Postgres BYPASSRLS role.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeItemRow).where(
                    KnowledgeItemRow.id == item_id,
                    KnowledgeItemRow.tenant_id == tenant_id,
                    KnowledgeItemRow.workspace_id == workspace_id,
                )
            )
            return result.scalars().first()

    async def get_item_across_workspaces(self, item_id: str) -> KnowledgeItemRow | None:
        """Cross-workspace lookup -- TRUSTED-CONTEXT callers only.

        Used by workers / admin sweepers that walk the table without
        a request scope (the row's own ``tenant_id`` / ``workspace_id``
        columns provide the scope ex post), backed by a Postgres
        BYPASSRLS role.

        Do NOT call this from request-scoped code paths -- the typed
        :meth:`get_item` is the only safe surface there.
        """
        async with self._session_factory() as session:
            return await session.get(KnowledgeItemRow, item_id)

    async def list_items(
        self,
        *,
        statuses: Sequence[str] | None = None,
        domains: Sequence[str] | None = None,
        jurisdictions: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[list[KnowledgeItemRow], int]:
        conditions: list[Any] = []
        if statuses:
            conditions.append(KnowledgeItemRow.status.in_(list(statuses)))
        if domains:
            conditions.append(KnowledgeItemRow.domain.in_(list(domains)))
        if jurisdictions:
            conditions.append(KnowledgeItemRow.jurisdiction.in_(list(jurisdictions)))
        if tenant_id:
            conditions.append(KnowledgeItemRow.tenant_id == tenant_id)
        if workspace_id:
            conditions.append(KnowledgeItemRow.workspace_id == workspace_id)

        async with self._session_factory() as session:
            stmt = select(KnowledgeItemRow)
            if conditions:
                stmt = stmt.where(*conditions)
            stmt = stmt.order_by(KnowledgeItemRow.updated_at.desc()).limit(limit).offset(offset)
            rows = list((await session.execute(stmt)).scalars().all())

            count_stmt = select(func.count()).select_from(KnowledgeItemRow)
            if conditions:
                count_stmt = count_stmt.where(*conditions)
            total = int((await session.execute(count_stmt)).scalar() or 0)
            return rows, total

    async def upsert_item(self, row: KnowledgeItemRow) -> KnowledgeItemRow:
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged

    async def claim_status_transition(
        self,
        item_id: str,
        *,
        from_statuses: Sequence[str],
        to_status: str,
        superseded_by_item_id: str | None = None,
        retired_reason: str | None = None,
        mark_retired_at: bool = False,
    ) -> KnowledgeItemRow | None:
        """Atomic ``status`` flip on an item, gated by ``from_statuses``.

        Used by :class:`KnowledgeService.supersede` / ``retire`` to
        defeat the check-then-act race: two operators driving the same
        lifecycle transition would otherwise both pass the local
        status guard and both call ``upsert_item``; last writer wins
        on the lifecycle pointers (``superseded_by_item_id`` /
        ``retired_at``).

        Returns the updated row when the caller wins the race;
        ``None`` when the item is in a status outside
        ``from_statuses`` (already transitioned or unknown id). The
        service layer maps ``None`` to the typed 4xx contract.
        """
        async with self.session() as session:
            values: dict[str, Any] = {"status": to_status}
            if superseded_by_item_id is not None:
                values["superseded_by_item_id"] = superseded_by_item_id
            if mark_retired_at:
                values["retired_at"] = func.now()
            if retired_reason is not None:
                values["retired_reason"] = retired_reason
            result = await session.execute(
                update(KnowledgeItemRow)
                .where(
                    KnowledgeItemRow.id == item_id,
                    KnowledgeItemRow.status.in_(list(from_statuses)),
                )
                .values(**values)
                .returning(KnowledgeItemRow)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.refresh(row)
            return row

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def list_versions(
        self,
        item_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> list[KnowledgeVersionRow]:
        """Return every version for ``item_id``, scoped to ``(tenant, workspace)``.

        Scope kwargs are MANDATORY. Cross-workspace history reads
        return an empty list instead of leaking.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeVersionRow)
                .where(
                    KnowledgeVersionRow.knowledge_item_id == item_id,
                    KnowledgeVersionRow.tenant_id == tenant_id,
                    KnowledgeVersionRow.workspace_id == workspace_id,
                )
                .order_by(KnowledgeVersionRow.version.asc())
            )
            return list(result.scalars().all())

    async def get_version(
        self,
        item_id: str,
        version: int,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> KnowledgeVersionRow | None:
        """Look up one version of an item, scoped to ``(tenant, workspace)``."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeVersionRow).where(
                    KnowledgeVersionRow.knowledge_item_id == item_id,
                    KnowledgeVersionRow.version == version,
                    KnowledgeVersionRow.tenant_id == tenant_id,
                    KnowledgeVersionRow.workspace_id == workspace_id,
                )
            )
            return result.scalars().first()

    async def latest_version_number(self, item_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.max(KnowledgeVersionRow.version)).where(
                    KnowledgeVersionRow.knowledge_item_id == item_id
                )
            )
            return int(result.scalar() or 0)

    async def add_version(self, row: KnowledgeVersionRow) -> KnowledgeVersionRow:
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def upsert_version_status(self, row: KnowledgeVersionRow) -> KnowledgeVersionRow:
        """Merge status / supersession pointers on an existing version row.

        Used by ``KnowledgeService.update`` to flip the previous
        version's status to ``superseded`` without rewriting the
        version content.
        """
        async with self.session() as session:
            merged = await session.merge(row)
            await session.flush()
            return merged

    # ------------------------------------------------------------------
    # Citations
    # ------------------------------------------------------------------

    async def list_citations(self, version_id: str) -> list[CitationRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CitationRow).where(CitationRow.knowledge_version_id == version_id)
            )
            return list(result.scalars().all())

    async def add_citations(self, rows: Iterable[CitationRow]) -> int:
        prepared = list(rows)
        if not prepared:
            return 0
        async with self.session() as session:
            session.add_all(prepared)
            await session.flush()
            return len(prepared)

    async def lookup_published_citations_for_chunks(
        self,
        chunk_ids: Sequence[str],
    ) -> dict[str, KnowledgeLink]:
        """Resolve the chunk -> knowledge linkage for the retrieval hit hydration.

        Returns a mapping ``chunk_id -> KnowledgeLink`` for chunks
        that are cited by **the current version** of any knowledge
        item. Older / superseded versions are ignored so the Hit DTO
        points at the live canonical row, not a historical one.

        The lookup is one round-trip regardless of the number of
        chunks -- a single SELECT joining ``canon_citations`` against
        ``canon_knowledge_versions`` + ``canon_knowledge_items`` and
        filtering by ``version == item.current_version``. When a chunk
        is cited by multiple knowledge items (rare but legal), the
        most recently updated item wins.

        The link carries the full knowledge-version dimensions
        (status, domain, jurisdiction, tags) so the retrieval
        post-filter can match on them without a second repo
        round-trip per hit.
        """
        ids = list(chunk_ids)
        if not ids:
            return {}
        async with self._session_factory() as session:
            stmt = (
                select(
                    CitationRow.chunk_id,
                    KnowledgeVersionRow.knowledge_item_id,
                    KnowledgeVersionRow.version,
                    KnowledgeVersionRow.status,
                    KnowledgeVersionRow.domain,
                    KnowledgeVersionRow.jurisdiction,
                    KnowledgeVersionRow.tags_json,
                    KnowledgeItemRow.updated_at,
                )
                .join(
                    KnowledgeVersionRow,
                    KnowledgeVersionRow.id == CitationRow.knowledge_version_id,
                )
                .join(
                    KnowledgeItemRow,
                    KnowledgeItemRow.id == KnowledgeVersionRow.knowledge_item_id,
                )
                .where(
                    CitationRow.chunk_id.in_(ids),
                    KnowledgeVersionRow.version == KnowledgeItemRow.current_version,
                )
                .order_by(KnowledgeItemRow.updated_at.desc())
            )
            rows = (await session.execute(stmt)).all()

        resolved: dict[str, KnowledgeLink] = {}
        for chunk_id, item_id, version, status, domain, jurisdiction, tags_json, _updated_at in rows:
            if chunk_id is None or chunk_id in resolved:
                continue
            resolved[chunk_id] = KnowledgeLink(
                item_id=item_id,
                version=int(version),
                status=str(status),
                domain=str(domain),
                jurisdiction=str(jurisdiction),
                tags=tuple(tags_json or ()),
            )
        return resolved

    async def resolve_source_ids_for_items(
        self,
        item_ids: Sequence[str],
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> set[str]:
        """Source ids cited by the **current version** of the given items.

        The whole-document analogue of the retrieval path's chunk-level
        ``knowledge_item_ids`` filter: where retrieval matches a chunk hit
        whose chunk is cited by one of the items, this returns every
        ``source_id`` that those items' live citations point at. One
        round-trip -- a single SELECT joining ``canon_citations`` against
        ``canon_knowledge_versions`` + ``canon_knowledge_items`` filtered by
        ``version == item.current_version`` and scoped to
        ``(tenant_id, workspace_id)``. Superseded versions are ignored so the
        result reflects the live canonical citation set. Returns an empty set
        when ``item_ids`` is empty or none of the items cite any source.
        """
        ids = list(item_ids)
        if not ids:
            return set()
        async with self._session_factory() as session:
            stmt = (
                select(CitationRow.source_id)
                .join(
                    KnowledgeVersionRow,
                    KnowledgeVersionRow.id == CitationRow.knowledge_version_id,
                )
                .join(
                    KnowledgeItemRow,
                    KnowledgeItemRow.id == KnowledgeVersionRow.knowledge_item_id,
                )
                .where(
                    KnowledgeVersionRow.knowledge_item_id.in_(ids),
                    KnowledgeVersionRow.version == KnowledgeItemRow.current_version,
                    CitationRow.tenant_id == tenant_id,
                    CitationRow.workspace_id == workspace_id,
                )
            )
            rows = (await session.execute(stmt)).all()
        return {source_id for (source_id,) in rows if source_id is not None}
