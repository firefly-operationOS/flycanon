# Copyright 2026 Firefly Software Solutions Inc
"""Candidate endpoints under ``/api/v1/candidates``."""

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
    get_mapping,
    post_mapping,
    request_mapping,
)
from starlette.requests import Request

from flycanon.core.services.consolidation.handlers import (
    AcceptCandidateCommand,
    GetCandidateQuery,
    ListCandidatesQuery,
    ProposeCandidatesCommand,
    RejectCandidateCommand,
)
from flycanon.interfaces.dtos.candidate import (
    AcceptCandidateRequest,
    CandidateRecord,
    CandidatesPage,
    ProposeCandidateRequest,
    RejectCandidateRequest,
)
from flycanon.interfaces.enums import CandidateStatus, Domain
from flycanon.web.conventions import TenantContext, tenant_context_from_request


@rest_controller
@request_mapping("/api/v1/candidates")
class CandidatesController:
    """REST adapter for the pre-canonical proposal lifecycle."""

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @post_mapping(":propose", status_code=201)
    async def propose(
        self,
        http_request: Request,
        request: Valid[Body[ProposeCandidateRequest]],
    ) -> list[CandidateRecord]:
        """Run the consolidation stage against a source and persist
        every resulting candidate in ``proposed`` status."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            ProposeCandidatesCommand(
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    @get_mapping("")
    async def list_candidates(
        self,
        http_request: Request,
        status: QueryParam[str] = "",
        source_id: QueryParam[str] = "",
        domain: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> CandidatesPage:
        ctx: TenantContext = tenant_context_from_request(http_request)
        statuses = [CandidateStatus(s) for s in _split_csv(status)] if status else []
        return await self._queries.query(
            ListCandidatesQuery(
                statuses=statuses,
                source_id=source_id or None,
                domain=Domain(domain) if domain else None,
                limit=limit,
                offset=offset,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @get_mapping("/{candidate_id}")
    async def get_candidate(
        self,
        http_request: Request,
        candidate_id: PathVar[str],
    ) -> CandidateRecord:
        ctx: TenantContext = tenant_context_from_request(http_request)
        record = await self._queries.query(
            GetCandidateQuery(
                candidate_id=candidate_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        if record is None:
            raise ResourceNotFoundException(f"candidate {candidate_id!r} not found")
        return record

    @post_mapping("/{candidate_id}:accept")
    async def accept(
        self,
        http_request: Request,
        candidate_id: PathVar[str],
        request: Valid[Body[AcceptCandidateRequest]],
    ) -> CandidateRecord:
        """Materialise the candidate as a knowledge version."""
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            AcceptCandidateCommand(
                candidate_id=candidate_id,
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )

    @post_mapping("/{candidate_id}:reject")
    async def reject(
        self,
        http_request: Request,
        candidate_id: PathVar[str],
        request: Valid[Body[RejectCandidateRequest]],
    ) -> CandidateRecord:
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._commands.send(
            RejectCandidateCommand(
                candidate_id=candidate_id,
                request=request,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
                actor=ctx.actor,
            )
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]
