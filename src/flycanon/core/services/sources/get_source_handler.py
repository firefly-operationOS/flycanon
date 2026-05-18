# Copyright 2026 Firefly Software Solutions Inc
"""``GetSourceHandler`` -- read a single source row by id."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.core.mappers import to_source_record
from flycanon.interfaces.dtos.source import SourceRecord
from flycanon.models.repositories import SourceRepository


@dataclass(frozen=True)
class GetSourceQuery(Query[SourceRecord | None]):
    source_id: str


@query_handler
@service
class GetSourceHandler(QueryHandler[GetSourceQuery, SourceRecord | None]):
    def __init__(self, repository: SourceRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetSourceQuery) -> SourceRecord | None:
        row = await self._repository.get(query.source_id)
        if row is None:
            return None
        return to_source_record(row)
