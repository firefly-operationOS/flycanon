# Copyright 2026 Firefly Software Solutions Inc
"""CQRS handlers for the taxonomy registry."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, Query, QueryHandler, command_handler, query_handler

from flycanon.core.mappers import to_taxonomy_node
from flycanon.core.services.taxonomy import TaxonomyService
from flycanon.interfaces.dtos.taxonomy import (
    CreateTaxonomyNodeRequest,
    TaxonomyNode,
    TaxonomyTree,
)


@dataclass(frozen=True)
class CreateTaxonomyNodeCommand(Command[TaxonomyNode]):
    request: CreateTaxonomyNodeRequest
    actor: str | None = None
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None


@command_handler
@service
class CreateTaxonomyNodeHandler(CommandHandler[CreateTaxonomyNodeCommand, TaxonomyNode]):
    def __init__(self, taxonomy: TaxonomyService) -> None:
        super().__init__()
        self._taxonomy = taxonomy

    async def do_handle(self, command: CreateTaxonomyNodeCommand) -> TaxonomyNode:
        row = await self._taxonomy.add_node(
            command.request,
            tenant_id=command.tenant_id or "default",
            workspace_id=command.workspace_id or "default",
            actor=command.actor,
            correlation_id=command.correlation_id,
        )
        return to_taxonomy_node(row)


@dataclass(frozen=True)
class GetTaxonomyQuery(Query[TaxonomyTree]):
    tenant_id: str | None = None
    workspace_id: str | None = None


@query_handler
@service
class GetTaxonomyHandler(QueryHandler[GetTaxonomyQuery, TaxonomyTree]):
    def __init__(self, taxonomy: TaxonomyService) -> None:
        super().__init__()
        self._taxonomy = taxonomy

    async def do_handle(self, query: GetTaxonomyQuery) -> TaxonomyTree:
        # Idempotent lazy seed -- guarantees the documented
        # "ships a default taxonomy seeded from the workshop personas"
        # contract holds on a fresh deploy. ``ensure_default_seed`` is a
        # no-op when the root nodes already exist, so the cost on the
        # hot path is one indexed SELECT.
        await self._taxonomy.ensure_default_seed()
        rows = await self._taxonomy.list_all()
        return TaxonomyTree(nodes=[to_taxonomy_node(r) for r in rows])


__all__ = [
    "CreateTaxonomyNodeCommand",
    "CreateTaxonomyNodeHandler",
    "GetTaxonomyHandler",
    "GetTaxonomyQuery",
]
