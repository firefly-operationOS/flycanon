# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge graph visualisation endpoint -- ``/api/v1/knowledge:graph``.

Kept in its own controller (rather than landing under
``KnowledgeController``) so the colon-separated action verb
``:graph`` doesn't fight the ``/{item_id}`` catch-all on the
parent controller. Routing precedence is the responsibility of the
``@request_mapping`` registration; declaring the verb here gives
us a clean ``/api/v1/knowledge:graph`` namespace.
"""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import QueryParam, get_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.knowledge.handlers import (
    GetKnowledgeGraphMermaidQuery,
    GetKnowledgeGraphQuery,
)
from flycanon.interfaces.dtos.graph import KnowledgeGraph, MermaidGraph
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus
from flycanon.web.conventions import TenantContext, tenant_context_from_request


@rest_controller
@request_mapping("/api/v1")
class KnowledgeGraphController:
    """Graph visualisation surface."""

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("/knowledge:graph")
    async def get_graph(
        self,
        http_request: Request,
        format: QueryParam[str] = "json",
        domain: QueryParam[str] = "",
        jurisdiction: QueryParam[str] = "",
        status: QueryParam[str] = "",
        # Use a string-typed flag and parse it ourselves -- pyfly's
        # ``QueryParam[bool]`` coerces every non-empty value to True,
        # which silently breaks ``?include_sources=false``. The
        # explicit allow-list below leaves the meaning unambiguous.
        include_sources: QueryParam[str] = "true",
        limit: QueryParam[int] = 500,
    ) -> KnowledgeGraph | MermaidGraph:
        """Render the knowledge graph.

        Query params:

        * ``format`` -- ``json`` (default) or ``mermaid``.
        * ``domain`` / ``jurisdiction`` / ``status`` -- comma-
          separated filter lists narrowing the rendered set.
        * ``include_sources`` -- when true, ``canon_sources`` cited
          by at least one rendered item are added as nodes with
          ``cites`` edges linking them to the citing item.
        * ``limit`` -- caps the number of knowledge items rendered
          (graph builds dominated by the citation join scale
          linearly with this).

        Returns either:

        * :class:`KnowledgeGraph` with flat ``nodes`` + ``edges``
          arrays consumable by D3 / Cytoscape / react-flow.
        * :class:`MermaidGraph` carrying a ``graph LR`` Mermaid
          block ready to drop in a Markdown viewer.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        domains = [Domain(d) for d in _split_csv(domain)] if domain else []
        jurisdictions = [Jurisdiction(j) for j in _split_csv(jurisdiction)] if jurisdiction else []
        statuses = [KnowledgeStatus(s) for s in _split_csv(status)] if status else []

        if format.lower() == "mermaid":
            return await self._queries.query(
                GetKnowledgeGraphMermaidQuery(
                    domains=domains,
                    jurisdictions=jurisdictions,
                    statuses=statuses,
                    include_sources=_parse_bool(include_sources),
                    limit=limit,
                    tenant_id=ctx.tenant_id,
                    workspace_id=ctx.workspace_id,
                )
            )
        return await self._queries.query(
            GetKnowledgeGraphQuery(
                domains=domains,
                jurisdictions=jurisdictions,
                statuses=statuses,
                include_sources=_parse_bool(include_sources),
                limit=limit,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _parse_bool(value: str) -> bool:
    """Truthy values: ``true``, ``1``, ``yes``, ``on``. Anything else = False."""
    return value.strip().lower() in {"true", "1", "yes", "on"}
