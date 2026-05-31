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
    to_knowledge_relation,
    to_knowledge_version,
)
from flycanon.core.services.knowledge import (
    KnowledgeDiffService,
    KnowledgeGraphService,
    KnowledgeRelationService,
    KnowledgeService,
    ProvenanceService,
)
from flycanon.interfaces.dtos.graph import KnowledgeGraph, MermaidGraph
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
from flycanon.interfaces.dtos.relation import (
    CreateRelationRequest,
    KnowledgeRelation,
    KnowledgeRelations,
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
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor: str | None = None


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
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            correlation_id=command.correlation_id,
        )
        citations = await self._repository.list_citations(version_row.id)
        return to_knowledge_version(version_row, citations=citations)


@dataclass(frozen=True)
class UpdateKnowledgeCommand(Command[KnowledgeVersion]):
    item_id: str
    request: UpdateKnowledgeRequest
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor: str | None = None


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
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            correlation_id=command.correlation_id,
        )
        citations = await self._repository.list_citations(version_row.id)
        return to_knowledge_version(version_row, citations=citations)


@dataclass(frozen=True)
class SupersedeKnowledgeCommand(Command[KnowledgeItem]):
    item_id: str
    request: SupersedeKnowledgeRequest
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor: str | None = None


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
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            correlation_id=command.correlation_id,
        )
        return to_knowledge_item(row)


@dataclass(frozen=True)
class RetireKnowledgeCommand(Command[KnowledgeItem]):
    item_id: str
    request: RetireKnowledgeRequest
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor: str | None = None


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
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            correlation_id=command.correlation_id,
        )
        return to_knowledge_item(row)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GetKnowledgeQuery(Query[KnowledgeItem | None]):
    item_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetKnowledgeHandler(QueryHandler[GetKnowledgeQuery, KnowledgeItem | None]):
    def __init__(self, repository: KnowledgeRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetKnowledgeQuery) -> KnowledgeItem | None:
        row = await self._repository.get_item(
            query.item_id,
            tenant_id=query.tenant_id or "default",
            workspace_id=query.workspace_id or "default",
        )
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
    tenant_id: str | None = None
    workspace_id: str | None = None


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
            tenant_id=query.tenant_id,
            workspace_id=query.workspace_id,
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
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetKnowledgeHistoryHandler(QueryHandler[GetKnowledgeHistoryQuery, list[KnowledgeVersion]]):
    def __init__(self, repository: KnowledgeRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetKnowledgeHistoryQuery) -> list[KnowledgeVersion]:
        rows = await self._repository.list_versions(
            query.item_id,
            tenant_id=query.tenant_id or "default",
            workspace_id=query.workspace_id or "default",
        )
        versions: list[KnowledgeVersion] = []
        for row in rows:
            citations = await self._repository.list_citations(row.id)
            versions.append(to_knowledge_version(row, citations=citations))
        return versions


@dataclass(frozen=True)
class GetProvenanceQuery(Query[Provenance]):
    item_id: str
    version: int | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetProvenanceHandler(QueryHandler[GetProvenanceQuery, Provenance]):
    def __init__(self, provenance: ProvenanceService) -> None:
        super().__init__()
        self._provenance = provenance

    async def do_handle(self, query: GetProvenanceQuery) -> Provenance:
        raw = await self._provenance.resolve(
            query.item_id,
            query.version,
            tenant_id=query.tenant_id or "default",
            workspace_id=query.workspace_id or "default",
        )
        return Provenance.model_validate(raw)


@dataclass(frozen=True)
class GetKnowledgeDiffQuery(Query[KnowledgeVersionDiff]):
    item_id: str
    from_version: int
    to_version: int
    tenant_id: str | None = None
    workspace_id: str | None = None


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
            tenant_id=query.tenant_id or "default",
            workspace_id=query.workspace_id or "default",
        )


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddKnowledgeRelationCommand(Command[KnowledgeRelation]):
    from_item_id: str
    request: CreateRelationRequest
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor: str | None = None


@command_handler
@service
class AddKnowledgeRelationHandler(CommandHandler[AddKnowledgeRelationCommand, KnowledgeRelation]):
    def __init__(self, relations: KnowledgeRelationService) -> None:
        super().__init__()
        self._relations = relations

    async def do_handle(self, command: AddKnowledgeRelationCommand) -> KnowledgeRelation:
        row = await self._relations.add(
            command.from_item_id,
            command.request,
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            correlation_id=command.correlation_id,
        )
        return to_knowledge_relation(row)


@dataclass(frozen=True)
class RemoveKnowledgeRelationCommand(Command[None]):
    relation_id: str
    actor: str | None = None
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None


@command_handler
@service
class RemoveKnowledgeRelationHandler(CommandHandler[RemoveKnowledgeRelationCommand, None]):
    def __init__(self, relations: KnowledgeRelationService) -> None:
        super().__init__()
        self._relations = relations

    async def do_handle(self, command: RemoveKnowledgeRelationCommand) -> None:
        await self._relations.remove(
            command.relation_id,
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            actor=command.actor,
            correlation_id=command.correlation_id,
        )


@dataclass(frozen=True)
class ListKnowledgeRelationsQuery(Query[KnowledgeRelations]):
    item_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class ListKnowledgeRelationsHandler(QueryHandler[ListKnowledgeRelationsQuery, KnowledgeRelations]):
    def __init__(self, relations: KnowledgeRelationService) -> None:
        super().__init__()
        self._relations = relations

    async def do_handle(self, query: ListKnowledgeRelationsQuery) -> KnowledgeRelations:
        out_rows, in_rows = await self._relations.list_for_item(
            query.item_id,
            tenant_id=query.tenant_id or "default",
            workspace_id=query.workspace_id or "default",
        )
        return KnowledgeRelations(
            item_id=query.item_id,
            outgoing=[to_knowledge_relation(r) for r in out_rows],
            incoming=[to_knowledge_relation(r) for r in in_rows],
        )


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GetKnowledgeGraphQuery(Query[KnowledgeGraph]):
    domains: list[Domain] = field(default_factory=list)
    jurisdictions: list[Jurisdiction] = field(default_factory=list)
    statuses: list[KnowledgeStatus] = field(default_factory=list)
    include_sources: bool = True
    limit: int = 500
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetKnowledgeGraphHandler(QueryHandler[GetKnowledgeGraphQuery, KnowledgeGraph]):
    def __init__(self, graph_service: KnowledgeGraphService) -> None:
        super().__init__()
        self._graph = graph_service

    async def do_handle(self, query: GetKnowledgeGraphQuery) -> KnowledgeGraph:
        return await self._graph.build(
            domains=query.domains or None,
            jurisdictions=query.jurisdictions or None,
            statuses=query.statuses or None,
            include_sources=query.include_sources,
            limit=query.limit,
        )


@dataclass(frozen=True)
class GetKnowledgeGraphMermaidQuery(Query[MermaidGraph]):
    domains: list[Domain] = field(default_factory=list)
    jurisdictions: list[Jurisdiction] = field(default_factory=list)
    statuses: list[KnowledgeStatus] = field(default_factory=list)
    include_sources: bool = True
    limit: int = 200
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetKnowledgeGraphMermaidHandler(QueryHandler[GetKnowledgeGraphMermaidQuery, MermaidGraph]):
    def __init__(self, graph_service: KnowledgeGraphService) -> None:
        super().__init__()
        self._graph = graph_service

    async def do_handle(self, query: GetKnowledgeGraphMermaidQuery) -> MermaidGraph:
        return await self._graph.build_mermaid(
            domains=query.domains or None,
            jurisdictions=query.jurisdictions or None,
            statuses=query.statuses or None,
            include_sources=query.include_sources,
            limit=query.limit,
        )


__all__ = [
    "AddKnowledgeRelationCommand",
    "AddKnowledgeRelationHandler",
    "CreateKnowledgeCommand",
    "CreateKnowledgeHandler",
    "GetKnowledgeDiffHandler",
    "GetKnowledgeDiffQuery",
    "GetKnowledgeGraphHandler",
    "GetKnowledgeGraphMermaidHandler",
    "GetKnowledgeGraphMermaidQuery",
    "GetKnowledgeGraphQuery",
    "GetKnowledgeHandler",
    "GetKnowledgeHistoryHandler",
    "GetKnowledgeHistoryQuery",
    "GetKnowledgeQuery",
    "GetProvenanceHandler",
    "GetProvenanceQuery",
    "ListKnowledgeHandler",
    "ListKnowledgeQuery",
    "ListKnowledgeRelationsHandler",
    "ListKnowledgeRelationsQuery",
    "RemoveKnowledgeRelationCommand",
    "RemoveKnowledgeRelationHandler",
    "RetireKnowledgeCommand",
    "RetireKnowledgeHandler",
    "SupersedeKnowledgeCommand",
    "SupersedeKnowledgeHandler",
    "UpdateKnowledgeCommand",
    "UpdateKnowledgeHandler",
    "to_citation",
]
