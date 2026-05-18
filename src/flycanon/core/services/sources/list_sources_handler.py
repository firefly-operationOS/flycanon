# Copyright 2026 Firefly Software Solutions Inc
"""``ListSourcesHandler`` -- paginated source list with filters."""

from __future__ import annotations

from dataclasses import dataclass, field

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.mappers import to_source_record
from flycanon.interfaces.dtos.source import SourcesPage
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.repositories import SourceRepository


@dataclass(frozen=True)
class ListSourcesQuery(Query[SourcesPage]):
    statuses: list[SourceStatus] = field(default_factory=list)
    kinds: list[SourceKind] = field(default_factory=list)
    limit: int = 50
    offset: int = 0


@query_handler
@service
class ListSourcesHandler(QueryHandler[ListSourcesQuery, SourcesPage]):
    def __init__(self, repository: SourceRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListSourcesQuery) -> SourcesPage:
        rows, total = await self._repository.list_sources(
            statuses=[s.value for s in query.statuses] or None,
            kinds=[k.value for k in query.kinds] or None,
            limit=query.limit,
            offset=query.offset,
        )
        return SourcesPage(
            items=[to_source_record(r) for r in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )
