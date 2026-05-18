# Copyright 2026 Firefly Software Solutions Inc
"""Taxonomy endpoints under ``/api/v1/taxonomy``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, Valid, get_mapping, post_mapping, request_mapping

from flycanon.core.services.taxonomy.handlers import (
    CreateTaxonomyNodeCommand,
    GetTaxonomyQuery,
)
from flycanon.interfaces.dtos.taxonomy import (
    CreateTaxonomyNodeRequest,
    TaxonomyNode,
    TaxonomyTree,
)


@rest_controller
@request_mapping("/api/v1/taxonomy")
class TaxonomyController:
    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @get_mapping("")
    async def get_taxonomy(self) -> TaxonomyTree:
        """Return the full taxonomy as a flat list ordered breadth-first."""
        return await self._queries.query(GetTaxonomyQuery())

    @post_mapping("/nodes", status_code=201)
    async def create_node(
        self,
        request: Valid[Body[CreateTaxonomyNodeRequest]],
    ) -> TaxonomyNode:
        """Attach a new node to the tree."""
        return await self._commands.send(
            CreateTaxonomyNodeCommand(request=request, correlation_id=get_correlation_id())
        )
