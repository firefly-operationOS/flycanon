# Copyright 2026 Firefly Software Solutions Inc
"""Global exception advice -- maps domain errors to RFC 7807 problem details.

``pyfly`` already converts most standard exceptions; this advice
handles the domain-specific ones flycanon throws so the API speaks
``application/problem+json`` end to end.
"""

from __future__ import annotations

from typing import Any

from pyfly.web import controller_advice, exception_handler

from flycanon.core.services.consolidation.errors import (
    CandidateAlreadyDecided,
    CandidateNotFound,
    ConsolidationError,
)
from flycanon.core.services.ingestion.errors import (
    CorruptSource,
    EmptySource,
    IngestionError,
    UnsupportedSourceKind,
)
from flycanon.core.services.knowledge.errors import (
    InvalidSupersedeTarget,
    KnowledgeItemAlreadyRetired,
    KnowledgeItemNotFound,
    KnowledgeServiceError,
    KnowledgeVersionConflict,
    KnowledgeVersionNotFound,
)
from flycanon.interfaces.dtos.error import ProblemDetails


@controller_advice
class ExceptionAdvice:
    @exception_handler(KnowledgeItemNotFound)
    async def knowledge_item_not_found(self, exc: KnowledgeItemNotFound) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/knowledge-item-not-found",
            title="Knowledge item not found",
            status=404,
            code=exc.code,
            detail=str(exc),
            extensions={"item_id": exc.item_id},
        )

    @exception_handler(KnowledgeVersionNotFound)
    async def knowledge_version_not_found(self, exc: KnowledgeVersionNotFound) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/knowledge-version-not-found",
            title="Knowledge version not found",
            status=404,
            code=exc.code,
            detail=str(exc),
            extensions={"item_id": exc.item_id, "version": exc.version},
        )

    @exception_handler(KnowledgeItemAlreadyRetired)
    async def knowledge_item_already_retired(self, exc: KnowledgeItemAlreadyRetired) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/knowledge-item-already-retired",
            title="Knowledge item already retired",
            status=409,
            code=exc.code,
            detail=str(exc),
            extensions={"item_id": exc.item_id},
        )

    @exception_handler(InvalidSupersedeTarget)
    async def invalid_supersede_target(self, exc: InvalidSupersedeTarget) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/invalid-supersede-target",
            title="Invalid supersede target",
            status=409,
            code=exc.code,
            detail=str(exc),
            extensions={"item_id": exc.item_id, "target_id": exc.target_id},
        )

    @exception_handler(KnowledgeVersionConflict)
    async def knowledge_version_conflict(self, exc: KnowledgeVersionConflict) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/knowledge-version-conflict",
            title="Knowledge version conflict",
            status=409,
            code=exc.code,
            detail=str(exc),
            extensions={"item_id": exc.item_id, "attempted_version": exc.attempted_version},
        )

    @exception_handler(KnowledgeServiceError)
    async def knowledge_service_error(self, exc: KnowledgeServiceError) -> dict[str, Any]:
        # Catch-all for any subclass not handled above.
        return _problem(
            type_=f"https://flycanon.dev/problems/{exc.code.replace('_', '-')}",
            title="Knowledge service error",
            status=getattr(exc, "http_status", 400),
            code=exc.code,
            detail=str(exc),
        )

    @exception_handler(CandidateNotFound)
    async def candidate_not_found(self, exc: CandidateNotFound) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/candidate-not-found",
            title="Candidate not found",
            status=404,
            code=exc.code,
            detail=str(exc),
            extensions={"candidate_id": exc.candidate_id},
        )

    @exception_handler(CandidateAlreadyDecided)
    async def candidate_already_decided(self, exc: CandidateAlreadyDecided) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/candidate-already-decided",
            title="Candidate already decided",
            status=409,
            code=exc.code,
            detail=str(exc),
            extensions={"candidate_id": exc.candidate_id, "status": exc.status},
        )

    @exception_handler(ConsolidationError)
    async def consolidation_error(self, exc: ConsolidationError) -> dict[str, Any]:
        return _problem(
            type_=f"https://flycanon.dev/problems/{exc.code.replace('_', '-')}",
            title="Consolidation failed",
            status=getattr(exc, "http_status", 422),
            code=exc.code,
            detail=str(exc),
        )

    @exception_handler(UnsupportedSourceKind)
    async def unsupported_source_kind(self, exc: UnsupportedSourceKind) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/unsupported-source-kind",
            title="Unsupported source kind",
            status=415,
            code=exc.code,
            detail=str(exc),
            extensions={"kind": exc.kind},
        )

    @exception_handler(EmptySource)
    async def empty_source(self, exc: EmptySource) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/empty-source",
            title="Empty source",
            status=422,
            code=exc.code,
            detail=str(exc),
        )

    @exception_handler(CorruptSource)
    async def corrupt_source(self, exc: CorruptSource) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/corrupt-source",
            title="Corrupt source",
            status=422,
            code=exc.code,
            detail=str(exc),
            extensions={"kind": exc.kind},
        )

    @exception_handler(IngestionError)
    async def ingestion_error(self, exc: IngestionError) -> dict[str, Any]:
        return _problem(
            type_=f"https://flycanon.dev/problems/{exc.code.replace('_', '-')}",
            title="Source ingestion failed",
            status=getattr(exc, "http_status", 422),
            code=exc.code,
            detail=str(exc),
        )

    @exception_handler(ValueError)
    async def invalid_request(self, exc: ValueError) -> dict[str, Any]:
        return _problem(
            type_="https://flycanon.dev/problems/invalid-request",
            title="Invalid request",
            status=400,
            code="invalid_request",
            detail=str(exc),
        )


def _problem(
    *,
    type_: str,
    title: str,
    status: int,
    code: str,
    detail: str,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ProblemDetails(
        type=type_,
        title=title,
        status=status,
        code=code,
        detail=detail,
        extensions=extensions,
    ).model_dump(exclude_none=True)
