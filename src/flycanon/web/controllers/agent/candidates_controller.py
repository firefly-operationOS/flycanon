# Copyright 2026 Firefly Software Solutions Inc
"""Agent-tier candidates endpoints (``/api/v1/agent/candidates``).

One route mounted under ``/api/v1/agent/candidates``:

* ``POST /api/v1/agent/candidates:propose`` -- run the
  consolidation stage against a source and persist every
  resulting candidate in ``proposed`` status. Scope:
  ``agent.candidates:propose``. Mandatory ``Idempotency-Key``.

Reuses the **same** :class:`ProposeCandidatesCommand` the
user-tier :class:`CandidatesController` dispatches -- the only
delta is the auth layer (``X-Agent-Token`` replaces the operator
JWT) and the mandatory idempotency key. Candidate accept / reject
remain user-tier-only in this phase: an agent may surface
proposals but a human must adjudicate them.
"""

from __future__ import annotations

import logging

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, Valid, post_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.core.services.consolidation.handlers import (
    ProposeCandidatesCommand,
)
from flycanon.interfaces.dtos.candidate import (
    CandidateRecord,
    ProposeCandidateRequest,
)
from flycanon.web.controllers.agent._helpers import (
    require_idempotency_key,
    verify_agent_token,
)

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/agent/candidates")
class AgentCandidatesController:
    """REST adapter for the agent-tier propose surface."""

    def __init__(
        self,
        agent_token_service: AgentTokenService,
        commands: DefaultCommandBus,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._commands = commands

    @post_mapping(":propose", status_code=201)
    async def propose(
        self,
        http_request: Request,
        request: Valid[Body[ProposeCandidateRequest]],
    ) -> list[CandidateRecord]:
        """Propose candidates from an existing source (agent tier).

        Scope: ``agent.candidates:propose``. Mandatory
        ``Idempotency-Key`` header -- missing key returns
        ``400 missing_idempotency_key``. Wire shape and 4xx
        mapping mirror the user-tier
        ``POST /api/v1/candidates:propose`` byte-for-byte.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.candidates:propose",
        )
        require_idempotency_key(http_request)
        return await self._commands.send(
            ProposeCandidatesCommand(
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )


__all__ = ["AgentCandidatesController"]
