# Copyright 2026 Firefly Software Solutions Inc
"""FastAPI-level RFC 7807 exception handlers.

pyfly ships a ``@controller_advice`` mechanism for global exception
handlers (``flycanon.web.advice.exception_advice.ExceptionAdvice``)
but its FastAPI adapter only walks **controller-local**
``@exception_handler`` methods at request time -- the global advice
chain registered against ``@controller_advice`` beans is never
consulted by ``pyfly.web.adapters.fastapi.controller.lazy_endpoint``.
The Starlette adapter does walk both layers; the FastAPI adapter
doesn't.

The net effect, pre-fix: every typed domain exception leaks one of
two ways. CQRS-dispatched mutations bubble up wrapped as
``CommandProcessingException`` (HTTP 400 with code
``COMMAND_PROCESSING_ERROR``, the generic ``BusinessException``
mapping). Direct-raise paths (``ValueError`` in a controller helper,
ingestion errors thrown outside the bus) hit pyfly's default
``Exception`` handler and surface as ``500 INTERNAL_ERROR``. The
RFC 7807 problem-details we wired up are nowhere on the wire.

This module re-registers the same mapping table directly with the
FastAPI app at startup, side-stepping the broken advice scan. It
also unwraps ``CommandProcessingException`` /
``QueryProcessingException`` so the typed ``cause`` raised by the
service layer reaches its matching handler -- our typed 4xx codes
finally arrive with the right HTTP status and ``code`` slug.

Once pyfly's FastAPI adapter is upgraded to walk global advice
beans (we keep ``ExceptionAdvice`` around for that day so the
upgrade is a no-op), this file can be deleted.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pyfly.cqrs.exceptions import (
    CommandProcessingException,
    QueryProcessingException,
)
from pyfly.kernel import ResourceNotFoundException

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
from flycanon.core.services.knowledge.relation_service import (
    InvalidRelationError,
    RelationConflictError,
    RelationNotFoundError,
)
from flycanon.interfaces.dtos.error import ProblemDetails


def _problem(
    *,
    type_: str,
    title: str,
    status: int,
    code: str,
    detail: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ProblemDetails(
        type=type_,
        title=title,
        status=status,
        code=code,
        detail=detail,
        extensions=extensions,
    ).model_dump(exclude_none=True)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def _knowledge_item_not_found(exc: KnowledgeItemNotFound) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/knowledge-item-not-found",
        title="Knowledge item not found",
        status=404,
        code=exc.code,
        detail=str(exc),
        extensions={"item_id": exc.item_id},
    )


def _knowledge_version_not_found(exc: KnowledgeVersionNotFound) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/knowledge-version-not-found",
        title="Knowledge version not found",
        status=404,
        code=exc.code,
        detail=str(exc),
        extensions={"item_id": exc.item_id, "version": exc.version},
    )


def _knowledge_item_already_retired(exc: KnowledgeItemAlreadyRetired) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/knowledge-item-already-retired",
        title="Knowledge item already retired",
        status=409,
        code=exc.code,
        detail=str(exc),
        extensions={"item_id": exc.item_id},
    )


def _invalid_supersede_target(exc: InvalidSupersedeTarget) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/invalid-supersede-target",
        title="Invalid supersede target",
        status=409,
        code=exc.code,
        detail=str(exc),
        extensions={"item_id": exc.item_id, "target_id": exc.target_id},
    )


def _knowledge_version_conflict(exc: KnowledgeVersionConflict) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/knowledge-version-conflict",
        title="Knowledge version conflict",
        status=409,
        code=exc.code,
        detail=str(exc),
        extensions={"item_id": exc.item_id, "attempted_version": exc.attempted_version},
    )


def _knowledge_service_error(exc: KnowledgeServiceError) -> JSONResponse:
    return _problem(
        type_=f"https://flycanon.dev/problems/{exc.code.replace('_', '-')}",
        title="Knowledge service error",
        status=getattr(exc, "http_status", 400),
        code=exc.code,
        detail=str(exc),
    )


def _candidate_not_found(exc: CandidateNotFound) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/candidate-not-found",
        title="Candidate not found",
        status=404,
        code=exc.code,
        detail=str(exc),
        extensions={"candidate_id": exc.candidate_id},
    )


def _candidate_already_decided(exc: CandidateAlreadyDecided) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/candidate-already-decided",
        title="Candidate already decided",
        status=409,
        code=exc.code,
        detail=str(exc),
        # ``exc.status`` would now return the HTTP ClassVar (409); the
        # candidate's lifecycle state lives on ``candidate_status``
        # after the FireflyHTTPException migration.
        extensions={"candidate_id": exc.candidate_id, "status": exc.candidate_status},
    )


def _consolidation_error(exc: ConsolidationError) -> JSONResponse:
    return _problem(
        type_=f"https://flycanon.dev/problems/{exc.code.replace('_', '-')}",
        title="Consolidation failed",
        status=getattr(exc, "http_status", 422),
        code=exc.code,
        detail=str(exc),
    )


def _unsupported_source_kind(exc: UnsupportedSourceKind) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/unsupported-source-kind",
        title="Unsupported source kind",
        status=415,
        code=exc.code,
        detail=str(exc),
        extensions={"kind": exc.kind},
    )


def _empty_source(exc: EmptySource) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/empty-source",
        title="Empty source",
        status=422,
        code=exc.code,
        detail=str(exc),
    )


def _corrupt_source(exc: CorruptSource) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/corrupt-source",
        title="Corrupt source",
        status=422,
        code=exc.code,
        detail=str(exc),
        extensions={"kind": exc.kind},
    )


def _ingestion_error(exc: IngestionError) -> JSONResponse:
    return _problem(
        type_=f"https://flycanon.dev/problems/{exc.code.replace('_', '-')}",
        title="Source ingestion failed",
        status=getattr(exc, "http_status", 422),
        code=exc.code,
        detail=str(exc),
    )


def _relation_conflict(exc: RelationConflictError) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/relation-already-exists",
        title="Relation already exists",
        status=409,
        code=exc.code,
        detail=str(exc),
    )


def _relation_not_found(exc: RelationNotFoundError) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/relation-not-found",
        title="Relation not found",
        status=404,
        code=exc.code,
        detail=str(exc),
    )


def _invalid_relation(exc: InvalidRelationError) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/invalid-relation",
        title="Invalid relation",
        status=422,
        code=exc.code,
        detail=str(exc),
    )


def _invalid_request(exc: ValueError) -> JSONResponse:
    return _problem(
        type_="https://flycanon.dev/problems/invalid-request",
        title="Invalid request",
        status=400,
        code="invalid_request",
        detail=str(exc),
    )


def _resource_not_found(exc: ResourceNotFoundException) -> JSONResponse:
    """Re-shape pyfly's native ``ResourceNotFoundException`` as RFC 7807.

    Several controllers raise this directly (knowledge GET, sources GET,
    candidates GET, conversations) and the default pyfly handler returns
    the ``{"error": {...}}`` envelope. Re-mapping to RFC 7807 keeps the
    wire format consistent with the typed business exceptions.
    """
    return _problem(
        type_="https://flycanon.dev/problems/resource-not-found",
        title="Resource not found",
        status=404,
        code=exc.code or "resource_not_found",
        detail=str(exc),
    )


# ---------------------------------------------------------------------------
# Dispatch table: typed business exception -> JSONResponse builder.
# Walked in MRO-depth order (most specific first) so a subclass match
# wins over its parent. Used both for direct-raise paths and for
# unwrapping the ``cause`` on CQRS-wrapped failures.
# ---------------------------------------------------------------------------


_TYPED_HANDLERS: tuple[tuple[type[BaseException], Any], ...] = (
    (KnowledgeItemNotFound, _knowledge_item_not_found),
    (KnowledgeVersionNotFound, _knowledge_version_not_found),
    (KnowledgeItemAlreadyRetired, _knowledge_item_already_retired),
    (InvalidSupersedeTarget, _invalid_supersede_target),
    (KnowledgeVersionConflict, _knowledge_version_conflict),
    (CandidateNotFound, _candidate_not_found),
    (CandidateAlreadyDecided, _candidate_already_decided),
    (UnsupportedSourceKind, _unsupported_source_kind),
    (EmptySource, _empty_source),
    (CorruptSource, _corrupt_source),
    (RelationConflictError, _relation_conflict),
    (RelationNotFoundError, _relation_not_found),
    (InvalidRelationError, _invalid_relation),
    # Parents come last so subclasses match first.
    (KnowledgeServiceError, _knowledge_service_error),
    (ConsolidationError, _consolidation_error),
    (IngestionError, _ingestion_error),
)


def _dispatch_typed(exc: BaseException) -> JSONResponse | None:
    """Run the first matching typed handler, or ``None`` when nothing matches."""
    for typ, handler in _TYPED_HANDLERS:
        if isinstance(exc, typ):
            return handler(exc)
    return None


def register_problem_handlers(app: FastAPI) -> None:
    """Install RFC 7807 exception handlers on the FastAPI app.

    Wires every typed business exception to its problem-details
    handler. ``CommandProcessingException`` and
    ``QueryProcessingException`` get unwrap shims that route on the
    ``cause`` -- pyfly's CQRS bus wraps **every** non-CQRS exception
    so the typed exception is otherwise lost.
    """

    async def typed(request: Request, exc: BaseException) -> JSONResponse:
        response = _dispatch_typed(exc)
        assert response is not None  # guarded by the handler registration
        return response

    for typ, _ in _TYPED_HANDLERS:
        app.add_exception_handler(typ, typed)  # type: ignore[arg-type]

    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        return _invalid_request(exc)

    app.add_exception_handler(ValueError, value_error)  # type: ignore[arg-type]

    async def not_found(request: Request, exc: ResourceNotFoundException) -> JSONResponse:
        return _resource_not_found(exc)

    app.add_exception_handler(ResourceNotFoundException, not_found)  # type: ignore[arg-type]

    async def command_processing(request: Request, exc: CommandProcessingException) -> JSONResponse:
        cause = exc.cause
        if cause is not None:
            response = _dispatch_typed(cause)
            if response is not None:
                return response
            if isinstance(cause, ResourceNotFoundException):
                return _resource_not_found(cause)
            if isinstance(cause, ValueError):
                return _invalid_request(cause)
        return _problem(
            type_="https://flycanon.dev/problems/command-processing-error",
            title="Command processing failed",
            status=400,
            code=exc.code or "command_processing_error",
            detail=str(exc),
        )

    async def query_processing(request: Request, exc: QueryProcessingException) -> JSONResponse:
        cause = exc.cause
        if cause is not None:
            response = _dispatch_typed(cause)
            if response is not None:
                return response
            if isinstance(cause, ResourceNotFoundException):
                return _resource_not_found(cause)
            if isinstance(cause, ValueError):
                return _invalid_request(cause)
        return _problem(
            type_="https://flycanon.dev/problems/query-processing-error",
            title="Query processing failed",
            status=400,
            code=exc.code or "query_processing_error",
            detail=str(exc),
        )

    app.add_exception_handler(CommandProcessingException, command_processing)  # type: ignore[arg-type]
    app.add_exception_handler(QueryProcessingException, query_processing)  # type: ignore[arg-type]
