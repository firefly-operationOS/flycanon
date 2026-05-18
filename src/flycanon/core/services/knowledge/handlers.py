# Copyright 2026 Firefly Software Solutions Inc
"""CQRS handlers for the knowledge lifecycle.

One module, several handlers -- each handler is small and the file
groups them by domain. Splitting per-handler is unnecessary churn at
this layer; the services upstream are the substantive code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, Query, QueryHandler, command_handler, query_handler

from flycanon.core.mappers import (
    to_citation,
    to_knowledge_item,
    to_knowledge_version,
)
from flycanon.core.services.knowledge import (
    KnowledgeDiffService,
    KnowledgeService,
    ProvenanceService,
)
from flycanon.interfaces.dtos.knowledge import (
    CreateKnowledgeRequest,
    KnowledgeItem,
    KnowledgeItemsPage,
    KnowledgeVersion,
    KnowledgeVersionDiff,
    Provenance,
    RetireKnowledgeRequest,
    SupersedeKnowledgeRequest,
    UpdateKnowledgeRequest,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus
from flycanon.models.repositories import KnowledgeRepository


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateKnowledgeCommand(Command[KnowledgeVersion]):
    request: CreateKnowledgeRequest
    correlation_id: str | None = None


@command_handler
@service
class CreateKnowledgeHandler(CommandHandler[CreateKnowledgeCommand, KnowledgeVersion]):
    def __init__(
        self,
        knowledge: KnowledgeService,
        repository: KnowledgeRepository,
    ) -> None:
        super().__init__()
        self._service = knowledge
        self._repository = repository

    async def do_handle(self, command: CreateKnowledgeCommand) -> KnowledgeVersion:
        version_row = await self._service.create(
            command.request,
            correlation_id=command.correlation_id,
        )
        citations = await self._repository.list_citations(version_row.id)
        return to_knowledge_version(version_row, citations=citations)


@dataclass(frozen=True)
class UpdateKnowledgeCommand(Command[KnowledgeVersion]):
    item_id: str
    request: UpdateKnowledgeRequest
    correlation_id: str | None = None


@command_handler
@service
class UpdateKnowledgeHandler(CommandHandler[UpdateKnowledgeCommand, KnowledgeVersion]):
    def __init__(
        self,
        knowledge: KnowledgeService,
        repository: KnowledgeRepository,
    ) -> None:
        super().__init__()
        self._service = knowledge
        self._repository = repository

    async def do_handle(self, command: UpdateKnowledgeCommand) -> KnowledgeVersion:
        version_row = await self._service.update(
            command.item_id,
            command.request,
            correlation_id=command.correlation_id,
        )
        citations = await self._repository.list_citations(version_row.id)
        return to_knowledge_version(version_row, citations=citations)


@dataclass(frozen=True)
class SupersedeKnowledgeCommand(Command[KnowledgeItem]):
    item_id: str
    request: SupersedeKnowledgeRequest
    correlation_id: str | None = None


@command_handler
@service
class SupersedeKnowledgeHandler(CommandHandler[SupersedeKnowledgeCommand, KnowledgeItem]):
    def __init__(self, knowledge: KnowledgeService) -> None:
        super().__init__()
        self._service = knowledge

    async def do_handle(self, command: SupersedeKnowledgeCommand) -> KnowledgeItem:
        row = await self._service.supersede(
            command.item_id,
            command.request,
            correlation_id=command.correlation_id,
        )
        return to_knowledge_item(row)


@dataclass(frozen=True)
class RetireKnowledgeCommand(Command[KnowledgeItem]):
    item_id: str
    request: RetireKnowledgeRequest
    correlation_id: str | None = None


@command_handler
@service
class RetireKnowledgeHandler(CommandHandler[RetireKnowledgeCommand, KnowledgeItem]):
    def __init__(self, knowledge: KnowledgeService) -> None:
        super().__init__()
        self._service = knowledge

    async def do_handle(self, command: RetireKnowledgeCommand) -> KnowledgeItem:
        row = await self._service.retire(
            command.item_id,
            command.request,
            correlation_id=command.correlation_id,
        )
        return to_knowledge_item(row)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GetKnowledgeQuery(Query[KnowledgeItem | None]):
    item_id: str


@query_handler
@service
class GetKnowledgeHandler(QueryHandler[GetKnowledgeQuery, KnowledgeItem | None]):
    def __init__(self, repository: KnowledgeRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetKnowledgeQuery) -> KnowledgeItem | None:
        row = await self._repository.get_item(query.item_id)
        if row is None:
            return None
        return to_knowledge_item(row)


@dataclass(frozen=True)
class ListKnowledgeQuery(Query[KnowledgeItemsPage]):
    statuses: list[KnowledgeStatus] = field(default_factory=list)
    domains: list[Domain] = field(default_factory=list)
    jurisdictions: list[Jurisdiction] = field(default_factory=list)
    limit: int = 50
    offset: int = 0


@query_handler
@service
class ListKnowledgeHandler(QueryHandler[ListKnowledgeQuery, KnowledgeItemsPage]):
    def __init__(self, repository: KnowledgeRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListKnowledgeQuery) -> KnowledgeItemsPage:
        rows, total = await self._repository.list_items(
            statuses=[s.value for s in query.statuses] or None,
            domains=[d.value for d in query.domains] or None,
            jurisdictions=[j.value for j in query.jurisdictions] or None,
            limit=query.limit,
            offset=query.offset,
        )
        return KnowledgeItemsPage(
            items=[to_knowledge_item(r) for r in rows],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )


@dataclass(frozen=True)
class GetKnowledgeHistoryQuery(Query[list[KnowledgeVersion]]):
    item_id: str


@query_handler
@service
class GetKnowledgeHistoryHandler(
    QueryHandler[GetKnowledgeHistoryQuery, list[KnowledgeVersion]]
):
    def __init__(self, repository: KnowledgeRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetKnowledgeHistoryQuery) -> list[KnowledgeVersion]:
        rows = await self._repository.list_versions(query.item_id)
        versions: list[KnowledgeVersion] = []
        for row in rows:
            citations = await self._repository.list_citations(row.id)
            versions.append(to_knowledge_version(row, citations=citations))
        return versions


@dataclass(frozen=True)
class GetProvenanceQuery(Query[Provenance]):
    item_id: str
    version: int | None = None


@query_handler
@service
class GetProvenanceHandler(QueryHandler[GetProvenanceQuery, Provenance]):
    def __init__(self, provenance: ProvenanceService) -> None:
        super().__init__()
        self._provenance = provenance

    async def do_handle(self, query: GetProvenanceQuery) -> Provenance:
        raw = await self._provenance.resolve(query.item_id, query.version)
        return Provenance.model_validate(raw)


@dataclass(frozen=True)
class GetKnowledgeDiffQuery(Query[KnowledgeVersionDiff]):
    item_id: str
    from_version: int
    to_version: int


@query_handler
@service
class GetKnowledgeDiffHandler(QueryHandler[GetKnowledgeDiffQuery, KnowledgeVersionDiff]):
    def __init__(self, diff_service: KnowledgeDiffService) -> None:
        super().__init__()
        self._diff = diff_service

    async def do_handle(self, query: GetKnowledgeDiffQuery) -> KnowledgeVersionDiff:
        return await self._diff.diff(
            item_id=query.item_id,
            from_version=query.from_version,
            to_version=query.to_version,
        )


__all__ = [
    "CreateKnowledgeCommand",
    "CreateKnowledgeHandler",
    "GetKnowledgeDiffHandler",
    "GetKnowledgeDiffQuery",
    "GetKnowledgeHandler",
    "GetKnowledgeHistoryHandler",
    "GetKnowledgeHistoryQuery",
    "GetKnowledgeQuery",
    "GetProvenanceHandler",
    "GetProvenanceQuery",
    "ListKnowledgeHandler",
    "ListKnowledgeQuery",
    "RetireKnowledgeCommand",
    "RetireKnowledgeHandler",
    "SupersedeKnowledgeCommand",
    "SupersedeKnowledgeHandler",
    "UpdateKnowledgeCommand",
    "UpdateKnowledgeHandler",
    "to_citation",
]
