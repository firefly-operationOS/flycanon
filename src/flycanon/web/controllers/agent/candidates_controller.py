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
    check_idempotency_replay,
    store_idempotent_response,
    verify_agent_token,
)
from flycanon.web.conventions import IdempotencyStore

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/agent/candidates")
class AgentCandidatesController:
    """REST adapter for the agent-tier propose surface."""

    def __init__(
        self,
        agent_token_service: AgentTokenService,
        commands: DefaultCommandBus,
        idempotency_store: IdempotencyStore,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._commands = commands
        self._idempotency_store = idempotency_store

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

        A replayed POST (same ``Idempotency-Key`` + same tenant
        within the store TTL) returns the cached candidate list
        re-hydrated from the stored JSON body, without re-running
        the consolidator. This is critical for the flyradar
        handoff path: a retried discovery must not produce
        duplicate candidate rows in the canon.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.candidates:propose",
        )
        scope = "agent.candidates:propose"
        cached = await check_idempotency_replay(http_request, self._idempotency_store, scope)
        # Re-hydrate each cached dict back into a
        # :class:`CandidateRecord`. The store always persists a list
        # for this route (propose returns the full batch). A
        # malformed entry (shouldn't happen with the in-memory store)
        # falls through to a dispatch by treating the cache as
        # absent.
        if cached is not None and isinstance(cached.body, list):
            return [CandidateRecord.model_validate(item) for item in cached.body]
        records = await self._commands.send(
            ProposeCandidatesCommand(
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )
        await store_idempotent_response(
            http_request,
            self._idempotency_store,
            scope,
            status=201,
            response=records,
        )
        return records


__all__ = ["AgentCandidatesController"]
