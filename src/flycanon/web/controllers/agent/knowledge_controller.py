# Copyright 2026 Firefly Software Solutions Inc
"""Agent-tier knowledge endpoints (``/api/v1/agent/knowledge``).

Two routes mounted under ``/api/v1/agent/knowledge``:

* ``GET /api/v1/agent/knowledge/{item_id}``             -- read a
  single knowledge item by id. Scope: ``agent.knowledge:read``.
* ``GET /api/v1/agent/knowledge/{item_id}/provenance``  -- resolve
  the citation graph for a knowledge version. Scope:
  ``agent.knowledge:read``. Optional ``version`` query param;
  defaults to the current version when omitted.

Both routes reuse the **same** query handlers the user-tier
:class:`KnowledgeController` dispatches (:class:`GetKnowledgeQuery`,
:class:`GetProvenanceQuery`) -- the only delta is the auth layer
(``X-Agent-Token`` replaces the operator JWT).
"""

from __future__ import annotations

import logging

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.web import (
    PathVar,
    QueryParam,
    get_mapping,
    request_mapping,
)
from starlette.requests import Request

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.core.services.knowledge.handlers import (
    GetKnowledgeQuery,
    GetProvenanceQuery,
)
from flycanon.interfaces.dtos.knowledge import (
    KnowledgeItem,
    Provenance,
)
from flycanon.web.controllers.agent._helpers import verify_agent_token

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/agent/knowledge")
class AgentKnowledgeController:
    """REST adapter for the agent-tier knowledge read surface.

    Wraps the existing CQRS queries without duplicating business
    logic. Writes (create / update / supersede / retire) are
    intentionally **not** exposed to the agent surface in this
    phase -- agents may consult the canon but not edit it.
    """

    def __init__(
        self,
        agent_token_service: AgentTokenService,
        queries: DefaultQueryBus,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._queries = queries

    @get_mapping("/{item_id}")
    async def get_knowledge(
        self,
        http_request: Request,
        item_id: PathVar[str],
    ) -> KnowledgeItem:
        """Fetch a single knowledge item by id (agent tier).

        Scope: ``agent.knowledge:read``. Returns the pointer view
        (current version metadata), not the version body. Unknown
        ``item_id`` (or any id outside the verified ctx's
        workspace) returns ``404 resource_not_found``.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.knowledge:read",
        )
        record = await self._queries.query(
            GetKnowledgeQuery(
                item_id=item_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        if record is None:
            raise ResourceNotFoundException(f"knowledge item {item_id!r} not found")
        return record

    @get_mapping("/{item_id}/provenance")
    async def get_provenance(
        self,
        http_request: Request,
        item_id: PathVar[str],
        version: QueryParam[int] = 0,
    ) -> Provenance:
        """Resolve the citation graph for ``(item_id, version)``.

        Scope: ``agent.knowledge:read``. ``version=0`` (or missing)
        resolves to the current version, matching the user-tier
        surface. Unknown ``item_id`` returns
        ``404 resource_not_found``; unknown ``version`` returns
        ``404 knowledge_version_not_found``.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.knowledge:read",
        )
        return await self._queries.query(
            GetProvenanceQuery(
                item_id=item_id,
                version=version or None,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )


__all__ = ["AgentKnowledgeController"]
