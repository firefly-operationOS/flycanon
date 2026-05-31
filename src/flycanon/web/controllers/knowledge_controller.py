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

"""Knowledge item endpoints under ``/api/v1/knowledge``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import (
    Body,
    PathVar,
    QueryParam,
    Valid,
    delete_mapping,
    get_mapping,
    post_mapping,
    put_mapping,
    request_mapping,
)
from starlette.requests import Request

from flycanon.core.services.knowledge.handlers import (
    AddKnowledgeRelationCommand,
    CreateKnowledgeCommand,
    GetKnowledgeDiffQuery,
    GetKnowledgeHistoryQuery,
    GetKnowledgeQuery,
    GetProvenanceQuery,
    ListKnowledgeQuery,
    ListKnowledgeRelationsQuery,
    RemoveKnowledgeRelationCommand,
    RetireKnowledgeCommand,
    SupersedeKnowledgeCommand,
    UpdateKnowledgeCommand,
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
from flycanon.interfaces.dtos.relation import (
    CreateRelationRequest,
    KnowledgeRelation,
    KnowledgeRelations,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus
from flycanon.web.conventions import TenantContext, tenant_context_from_request


@rest_controller
@request_mapping("/api/v1/knowledge")
class KnowledgeController:
    """REST adapter for the canonical-item lifecycle."""

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    # -- Reads ---------------------------------------------------------

    @get_mapping("")
    async def list_knowledge(
        self,
        http_request: Request,
        status: QueryParam[str] = "",
        domain: QueryParam[str] = "",
        jurisdiction: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> KnowledgeItemsPage:
        """Paginated, filterable list of knowledge items."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        statuses = [KnowledgeStatus(s) for s in _split_csv(status)] if status else []
        domains = [Domain(d) for d in _split_csv(domain)] if domain else []
        jurisdictions = [Jurisdiction(j) for j in _split_csv(jurisdiction)] if jurisdiction else []
        return await self._queries.query(
            ListKnowledgeQuery(
                statuses=statuses,
                domains=domains,
                jurisdictions=jurisdictions,
                limit=limit,
                offset=offset,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @get_mapping("/{item_id}")
    async def get_knowledge(
        self,
        http_request: Request,
        item_id: PathVar[str],
    ) -> KnowledgeItem:
        """Fetch a single knowledge item by id (returns the pointer
        view, not the body -- use the history endpoint for content)."""
        ctx: TenantContext = tenant_context_from_request(http_request)
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

    @get_mapping("/{item_id}/history")
    async def get_history(
        self,
        http_request: Request,
        item_id: PathVar[str],
    ) -> list[KnowledgeVersion]:
        """Full version history (oldest first)."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._queries.query(
            GetKnowledgeHistoryQuery(
                item_id=item_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @get_mapping("/{item_id}/provenance")
    async def get_provenance(
        self,
        http_request: Request,
        item_id: PathVar[str],
        version: QueryParam[int] = 0,
    ) -> Provenance:
        """Resolve the citation graph for ``(item_id, version)``."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._queries.query(
            GetProvenanceQuery(
                item_id=item_id,
                version=version or None,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @get_mapping("/{item_id}/diff")
    async def get_diff(
        self,
        http_request: Request,
        item_id: PathVar[str],
        from_version: QueryParam[int] = 0,
        to_version: QueryParam[int] = 0,
    ) -> KnowledgeVersionDiff:
        """Diff two versions of a knowledge item.

        ``from_version`` / ``to_version`` are 1-based and both
        required. Returns a structured diff: unified-diff body,
        scalar field changes (title / summary / domain /
        jurisdiction / status / tags), and citation set deltas
        (added / removed).

        Errors:

        * ``400`` -- either query param is 0 / missing.
        * ``404 knowledge_item_not_found`` -- item id is unknown.
        * ``404 knowledge_version_not_found`` -- one of the
          requested versions doesn't exist.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        if from_version <= 0 or to_version <= 0:
            raise ValueError(
                "Both from_version and to_version query params are required "
                "(use ?from_version=X&to_version=Y)."
            )
        return await self._queries.query(
            GetKnowledgeDiffQuery(
                item_id=item_id,
                from_version=from_version,
                to_version=to_version,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    # -- Writes --------------------------------------------------------

    @post_mapping("", status_code=201)
    async def create_knowledge(
        self,
        http_request: Request,
        request: Valid[Body[CreateKnowledgeRequest]],
    ) -> KnowledgeVersion:
        """Create a fresh knowledge item + version=1."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            CreateKnowledgeCommand(
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    @put_mapping("/{item_id}")
    async def update_knowledge(
        self,
        http_request: Request,
        item_id: PathVar[str],
        request: Valid[Body[UpdateKnowledgeRequest]],
    ) -> KnowledgeVersion:
        """Append a new version to the item (the previous version
        transitions to ``superseded``)."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            UpdateKnowledgeCommand(
                item_id=item_id,
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    @post_mapping("/{item_id}:supersede")
    async def supersede_knowledge(
        self,
        http_request: Request,
        item_id: PathVar[str],
        request: Valid[Body[SupersedeKnowledgeRequest]],
    ) -> KnowledgeItem:
        """Point the whole item at another canonical item."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            SupersedeKnowledgeCommand(
                item_id=item_id,
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    @post_mapping("/{item_id}:retire")
    async def retire_knowledge(
        self,
        http_request: Request,
        item_id: PathVar[str],
        request: Valid[Body[RetireKnowledgeRequest]],
    ) -> KnowledgeItem:
        """Withdraw the item permanently."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            RetireKnowledgeCommand(
                item_id=item_id,
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    # -- Relations -----------------------------------------------------

    @get_mapping("/{item_id}/relations")
    async def list_relations(
        self,
        http_request: Request,
        item_id: PathVar[str],
    ) -> KnowledgeRelations:
        """Outgoing + incoming relations for ``{item_id}``.

        The response splits by direction so a UI can render
        ``Depends on`` and ``Required by`` sections without
        filtering client-side.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._queries.query(
            ListKnowledgeRelationsQuery(
                item_id=item_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @post_mapping("/{item_id}/relations", status_code=201)
    async def add_relation(
        self,
        http_request: Request,
        item_id: PathVar[str],
        request: Valid[Body[CreateRelationRequest]],
    ) -> KnowledgeRelation:
        """Attach a typed relation rooted at ``{item_id}``.

        ``kind`` is one of ``related`` / ``depends_on`` /
        ``conflicts_with`` / ``replaces``. ``(from, to, kind)`` is
        unique -- re-asserting the same edge returns ``409
        relation_already_exists``.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            AddKnowledgeRelationCommand(
                from_item_id=item_id,
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    @delete_mapping("/{item_id}/relations/{relation_id}", status_code=204)
    async def remove_relation(
        self,
        http_request: Request,
        item_id: PathVar[str],  # noqa: ARG002 -- part of the URL contract
        relation_id: PathVar[str],
    ) -> None:
        """Drop one relation by id."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        await self._commands.send(
            RemoveKnowledgeRelationCommand(
                relation_id=relation_id,
                actor=ctx.actor,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]
