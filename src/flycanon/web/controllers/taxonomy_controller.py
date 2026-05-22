# Copyright 2026 Firefly Software Solutions Inc
"""Taxonomy endpoints under ``/api/v1/taxonomy``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, Valid, get_mapping, post_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.taxonomy.handlers import (
    CreateTaxonomyNodeCommand,
    GetTaxonomyQuery,
)
from flycanon.interfaces.dtos.taxonomy import (
    CreateTaxonomyNodeRequest,
    TaxonomyNode,
    TaxonomyTree,
)
from flycanon.web.conventions import TenantContext, tenant_context_from_request


@rest_controller
@request_mapping("/api/v1/taxonomy")
class TaxonomyController:
    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @get_mapping("")
    async def get_taxonomy(self, http_request: Request) -> TaxonomyTree:
        """Return the full taxonomy as a flat list ordered breadth-first."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._queries.query(
            GetTaxonomyQuery(
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @post_mapping("/nodes", status_code=201)
    async def create_node(
        self,
        http_request: Request,
        request: Valid[Body[CreateTaxonomyNodeRequest]],
    ) -> TaxonomyNode:
        """Attach a new node to the tree."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            CreateTaxonomyNodeCommand(
                request=request,
                correlation_id=get_correlation_id(),
                actor=ctx.actor,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
