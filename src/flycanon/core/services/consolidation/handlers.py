# Copyright 2026 Firefly Software Solutions Inc
"""CQRS handlers for the candidate lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, Query, QueryHandler, command_handler, query_handler

from flycanon.core.mappers import to_candidate_record
from flycanon.core.services.consolidation.candidate_service import CandidateService
from flycanon.interfaces.dtos.candidate import (
    AcceptCandidateRequest,
    CandidateRecord,
    CandidatesPage,
    ProposeCandidateRequest,
    RejectCandidateRequest,
)
from flycanon.interfaces.enums import CandidateStatus, Domain
from flycanon.models.repositories import CandidateRepository


@dataclass(frozen=True)
class ProposeCandidatesCommand(Command[list[CandidateRecord]]):
    request: ProposeCandidateRequest
    correlation_id: str | None = None


@command_handler
@service
class ProposeCandidatesHandler(
    CommandHandler[ProposeCandidatesCommand, list[CandidateRecord]]
):
    def __init__(self, candidates: CandidateService) -> None:
        super().__init__()
        self._candidates = candidates

    async def do_handle(
        self, command: ProposeCandidatesCommand
    ) -> list[CandidateRecord]:
        rows = await self._candidates.propose_from_source(
            command.request, correlation_id=command.correlation_id
        )
        return [to_candidate_record(r) for r in rows]


@dataclass(frozen=True)
class AcceptCandidateCommand(Command[CandidateRecord]):
    candidate_id: str
    request: AcceptCandidateRequest
    correlation_id: str | None = None


@command_handler
@service
class AcceptCandidateHandler(CommandHandler[AcceptCandidateCommand, CandidateRecord]):
    def __init__(self, candidates: CandidateService) -> None:
        super().__init__()
        self._candidates = candidates

    async def do_handle(self, command: AcceptCandidateCommand) -> CandidateRecord:
        row = await self._candidates.accept(
            command.candidate_id, command.request, correlation_id=command.correlation_id
        )
        return to_candidate_record(row)


@dataclass(frozen=True)
class RejectCandidateCommand(Command[CandidateRecord]):
    candidate_id: str
    request: RejectCandidateRequest
    correlation_id: str | None = None


@command_handler
@service
class RejectCandidateHandler(CommandHandler[RejectCandidateCommand, CandidateRecord]):
    def __init__(self, candidates: CandidateService) -> None:
        super().__init__()
        self._candidates = candidates

    async def do_handle(self, command: RejectCandidateCommand) -> CandidateRecord:
        row = await self._candidates.reject(
            command.candidate_id, command.request, correlation_id=command.correlation_id
        )
        return to_candidate_record(row)


@dataclass(frozen=True)
class GetCandidateQuery(Query[CandidateRecord | None]):
    candidate_id: str


@query_handler
@service
class GetCandidateHandler(QueryHandler[GetCandidateQuery, CandidateRecord | None]):
    def __init__(self, repository: CandidateRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetCandidateQuery) -> CandidateRecord | None:
        row = await self._repository.get(query.candidate_id)
        if row is None:
            return None
        return to_candidate_record(row)


@dataclass(frozen=True)
class ListCandidatesQuery(Query[CandidatesPage]):
    statuses: list[CandidateStatus] = field(default_factory=list)
    source_id: str | None = None
    domain: Domain | None = None
    limit: int = 50
    offset: int = 0


@query_handler
@service
class ListCandidatesHandler(QueryHandler[ListCandidatesQuery, CandidatesPage]):
    def __init__(self, repository: CandidateRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListCandidatesQuery) -> CandidatesPage:
        rows, total = await self._repository.list_candidates(
            statuses=[s.value for s in query.statuses] or None,
            source_id=query.source_id,
            domain=query.domain.value if query.domain else None,
            limit=query.limit,
            offset=query.offset,
        )
        return CandidatesPage(
            items=[to_candidate_record(r) for r in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )


__all__: list[str] = [
    "AcceptCandidateCommand",
    "AcceptCandidateHandler",
    "GetCandidateHandler",
    "GetCandidateQuery",
    "ListCandidatesHandler",
    "ListCandidatesQuery",
    "ProposeCandidatesCommand",
    "ProposeCandidatesHandler",
    "RejectCandidateCommand",
    "RejectCandidateHandler",
]
