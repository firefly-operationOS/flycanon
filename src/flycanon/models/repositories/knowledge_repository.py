# Copyright 2026 Firefly Software Solutions Inc
"""Async repository for knowledge items, versions, and citation rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
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

    async def get_item(self, item_id: str) -> KnowledgeItemRow | None:
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
    ) -> tuple[list[KnowledgeItemRow], int]:
        conditions: list[Any] = []
        if statuses:
            conditions.append(KnowledgeItemRow.status.in_(list(statuses)))
        if domains:
            conditions.append(KnowledgeItemRow.domain.in_(list(domains)))
        if jurisdictions:
            conditions.append(KnowledgeItemRow.jurisdiction.in_(list(jurisdictions)))

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

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def list_versions(self, item_id: str) -> list[KnowledgeVersionRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeVersionRow)
                .where(KnowledgeVersionRow.knowledge_item_id == item_id)
                .order_by(KnowledgeVersionRow.version.asc())
            )
            return list(result.scalars().all())

    async def get_version(
        self,
        item_id: str,
        version: int,
    ) -> KnowledgeVersionRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(KnowledgeVersionRow).where(
                    KnowledgeVersionRow.knowledge_item_id == item_id,
                    KnowledgeVersionRow.version == version,
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
