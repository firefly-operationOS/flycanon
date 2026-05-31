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

"""Conversation endpoints under ``/api/v1/conversations``."""

from __future__ import annotations

import logging

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, PathVar, Valid, get_mapping, post_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.conversations import (
    ConversationNotFound,
    ConversationService,
)
from flycanon.interfaces.dtos.conversation import (
    Conversation,
    CreateConversationRequest,
    CreateTurnRequest,
    TurnResponse,
)
from flycanon.web.conventions import TenantContext, tenant_context_from_request

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/conversations")
class ConversationsController:
    """Multi-turn chat sessions over the canon."""

    def __init__(
        self,
        commands: DefaultCommandBus,  # noqa: ARG002 -- reserved for future commands
        queries: DefaultQueryBus,  # noqa: ARG002 -- ditto
        service: ConversationService,
    ) -> None:
        self._service = service

    @post_mapping("", status_code=201)
    async def create_conversation(
        self,
        http_request: Request,
        request: Valid[Body[CreateConversationRequest]],
    ) -> Conversation:
        """Open a fresh conversation -- returns the empty session."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        row = await self._service.create(
            request,
            correlation_id=get_correlation_id(),
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            actor=ctx.actor,
        )
        return await self._service.get(
            row.id,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
        )

    @get_mapping("/{conversation_id}")
    async def get_conversation(
        self,
        http_request: Request,
        conversation_id: PathVar[str],
    ) -> Conversation:
        """Return the full session + turn history.

        The service-layer ``get`` requires ``(tenant_id,
        workspace_id)``; a same-tenant cross-workspace lookup raises
        :class:`ConversationNotFound` (404) rather than returning the
        foreign row.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        try:
            return await self._service.get(
                conversation_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        except ConversationNotFound as exc:
            raise ResourceNotFoundException(str(exc)) from exc

    @post_mapping("/{conversation_id}/turn", status_code=201)
    async def append_turn(
        self,
        http_request: Request,
        conversation_id: PathVar[str],
        request: Valid[Body[CreateTurnRequest]],
    ) -> TurnResponse:
        """Run the next turn and return the answer + citations."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        try:
            _conv, turn = await self._service.append_turn(
                conversation_id,
                request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        except ConversationNotFound as exc:
            raise ResourceNotFoundException(str(exc)) from exc
        # Reuse the existing turn -> DTO mapping by going through the
        # service's ``get`` method's helper would re-fetch all turns;
        # build the DTO directly to keep the response cheap.
        from flycanon.core.services.conversations.conversation_service import (
            _turn_to_dto,
        )

        return TurnResponse(
            conversation_id=conversation_id,
            turn=_turn_to_dto(turn),
        )
