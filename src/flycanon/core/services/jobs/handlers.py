# Copyright 2026 Firefly Software Solutions Inc
"""CQRS queries for the async-ingest job read surface."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon.interfaces.dtos.job import IngestJob, IngestJobEvent, IngestJobsPage
from flycanon.models.entities.ingest_job import IngestJobEventRow, IngestJobRow
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository


@dataclass(frozen=True)
class GetIngestJobQuery(Query[IngestJob | None]):
    job_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetIngestJobHandler(QueryHandler[GetIngestJobQuery, IngestJob | None]):
    def __init__(self, repository: IngestJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetIngestJobQuery) -> IngestJob | None:
        row = await self._repository.get(query.job_id)
        if row is None:
            return None
        return _to_dto(row)


@dataclass(frozen=True)
class ListIngestJobsQuery(Query[IngestJobsPage]):
    status: str | None = None
    limit: int = 50
    offset: int = 0
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class ListIngestJobsHandler(QueryHandler[ListIngestJobsQuery, IngestJobsPage]):
    def __init__(self, repository: IngestJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListIngestJobsQuery) -> IngestJobsPage:
        statuses = [query.status] if query.status else None
        rows = await self._repository.list_jobs(
            statuses=statuses,
            limit=query.limit,
            offset=query.offset,
            tenant_id=query.tenant_id,
            workspace_id=query.workspace_id,
        )
        return IngestJobsPage(
            items=[_to_dto(row) for row in rows],
            total=len(rows),
            offset=query.offset,
            limit=query.limit,
        )


@dataclass(frozen=True)
class ListIngestJobEventsQuery(Query[list[IngestJobEvent]]):
    job_id: str
    after_id: int | None = None
    limit: int = 200
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class ListIngestJobEventsHandler(QueryHandler[ListIngestJobEventsQuery, list[IngestJobEvent]]):
    def __init__(self, repository: IngestJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListIngestJobEventsQuery) -> list[IngestJobEvent]:
        rows = await self._repository.list_events(query.job_id, after_id=query.after_id, limit=query.limit)
        return [_event_to_dto(r) for r in rows]


def _to_dto(row: IngestJobRow) -> IngestJob:
    return IngestJob(
        id=row.id,
        status=row.status,
        source_id=row.source_id,
        attempts=row.attempts or 0,
        filename=row.filename,
        content_type=row.content_type,
        uri=row.uri,
        content_sha256=row.content_sha256,
        actor=row.actor,
        correlation_id=row.correlation_id,
        callback_url=row.callback_url,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
    )


def _event_to_dto(row: IngestJobEventRow) -> IngestJobEvent:
    return IngestJobEvent(
        id=row.id,
        job_id=row.job_id,
        stage=row.stage,
        message=row.message,
        payload=dict(row.payload_json or {}),
        occurred_at=row.occurred_at,
    )
