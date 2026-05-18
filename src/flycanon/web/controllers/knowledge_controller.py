# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge item endpoints under ``/api/v1/knowledge``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.web import (
    Body,
    PathVar,
    QueryParam,
    Valid,
    get_mapping,
    post_mapping,
    put_mapping,
    request_mapping,
)

from flycanon.core.services.knowledge.handlers import (
    CreateKnowledgeCommand,
    GetKnowledgeHistoryQuery,
    GetKnowledgeQuery,
    GetProvenanceQuery,
    ListKnowledgeQuery,
    RetireKnowledgeCommand,
    SupersedeKnowledgeCommand,
    UpdateKnowledgeCommand,
)
from flycanon.interfaces.dtos.knowledge import (
    CreateKnowledgeRequest,
    KnowledgeItem,
    KnowledgeItemsPage,
    KnowledgeVersion,
    Provenance,
    RetireKnowledgeRequest,
    SupersedeKnowledgeRequest,
    UpdateKnowledgeRequest,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


@rest_controller
@request_mapping("/api/v1/knowledge")
class KnowledgeController:
    """REST adapter for the canonical-item lifecycle."""

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    # -- Reads ---------------------------------------------------------

    @get_mapping("", tags=["Knowledge"])
    async def list_knowledge(
        self,
        status: QueryParam[str] = "",
        domain: QueryParam[str] = "",
        jurisdiction: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> KnowledgeItemsPage:
        """Paginated, filterable list of knowledge items."""
        statuses = [KnowledgeStatus(s) for s in _split_csv(status)] if status else []
        domains = [Domain(d) for d in _split_csv(domain)] if domain else []
        jurisdictions = (
            [Jurisdiction(j) for j in _split_csv(jurisdiction)] if jurisdiction else []
        )
        return await self._queries.query(
            ListKnowledgeQuery(
                statuses=statuses,
                domains=domains,
                jurisdictions=jurisdictions,
                limit=limit,
                offset=offset,
            )
        )

    @get_mapping("/{item_id}", tags=["Knowledge"])
    async def get_knowledge(self, item_id: PathVar[str]) -> KnowledgeItem:
        """Fetch a single knowledge item by id (returns the pointer
        view, not the body -- use the history endpoint for content)."""
        record = await self._queries.query(GetKnowledgeQuery(item_id=item_id))
        if record is None:
            raise ResourceNotFoundException(f"knowledge item {item_id!r} not found")
        return record

    @get_mapping("/{item_id}/history", tags=["Knowledge"])
    async def get_history(self, item_id: PathVar[str]) -> list[KnowledgeVersion]:
        """Full version history (oldest first)."""
        return await self._queries.query(GetKnowledgeHistoryQuery(item_id=item_id))

    @get_mapping("/{item_id}/provenance", tags=["Knowledge"])
    async def get_provenance(
        self,
        item_id: PathVar[str],
        version: QueryParam[int] = 0,
    ) -> Provenance:
        """Resolve the citation graph for ``(item_id, version)``."""
        return await self._queries.query(
            GetProvenanceQuery(item_id=item_id, version=version or None)
        )

    # -- Writes --------------------------------------------------------

    @post_mapping("", status_code=201, tags=["Knowledge"])
    async def create_knowledge(
        self,
        request: Valid[Body[CreateKnowledgeRequest]],
    ) -> KnowledgeVersion:
        """Create a fresh knowledge item + version=1."""
        return await self._commands.send(CreateKnowledgeCommand(request=request))

    @put_mapping("/{item_id}", tags=["Knowledge"])
    async def update_knowledge(
        self,
        item_id: PathVar[str],
        request: Valid[Body[UpdateKnowledgeRequest]],
    ) -> KnowledgeVersion:
        """Append a new version to the item (the previous version
        transitions to ``superseded``)."""
        return await self._commands.send(
            UpdateKnowledgeCommand(item_id=item_id, request=request)
        )

    @post_mapping("/{item_id}:supersede", tags=["Knowledge"])
    async def supersede_knowledge(
        self,
        item_id: PathVar[str],
        request: Valid[Body[SupersedeKnowledgeRequest]],
    ) -> KnowledgeItem:
        """Point the whole item at another canonical item."""
        return await self._commands.send(
            SupersedeKnowledgeCommand(item_id=item_id, request=request)
        )

    @post_mapping("/{item_id}:retire", tags=["Knowledge"])
    async def retire_knowledge(
        self,
        item_id: PathVar[str],
        request: Valid[Body[RetireKnowledgeRequest]],
    ) -> KnowledgeItem:
        """Withdraw the item permanently."""
        return await self._commands.send(
            RetireKnowledgeCommand(item_id=item_id, request=request)
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]
