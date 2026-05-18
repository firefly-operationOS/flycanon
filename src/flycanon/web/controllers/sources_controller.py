# Copyright 2026 Firefly Software Solutions Inc
"""Source intake + read endpoints under ``/api/v1/sources``."""

from __future__ import annotations

import base64
import logging

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
    request_mapping,
)

from flycanon.core.services.sources import (
    GetSourceQuery,
    ListSourcesQuery,
    SubmitSourceCommand,
)
from flycanon.interfaces.dtos.source import (
    SourceMetadata,
    SourceRecord,
    SourcesPage,
    SubmitSourceRequest,
)
from flycanon.interfaces.enums import SourceKind, SourceStatus

logger = logging.getLogger(__name__)


class SubmitSourceJsonPayload(SubmitSourceRequest):
    """JSON wrapper used when callers post a source by URL or base64 bytes.

    ``content_base64`` is the canonical body when the source isn't a
    file upload; ``uri`` lets the future fetcher pull bytes by URL
    (not implemented yet -- the handler raises if uri is set and
    content_base64 is empty).
    """

    content_base64: str | None = None
    filename: str | None = None
    content_type: str | None = None


@rest_controller
@request_mapping("/api/v1/sources")
class SourcesController:
    """Inbound source intake + lookups.

    Multipart file uploads land bytes alongside the metadata JSON; the
    JSON-only path accepts a ``content_base64`` payload for callers
    that prefer a single content-type. Both routes feed the same
    :class:`SubmitSourceCommand` handler.
    """

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @post_mapping("", status_code=201, tags=["Sources"])
    async def submit_json(
        self,
        payload: Valid[Body[SubmitSourceJsonPayload]],
    ) -> SourceRecord:
        """Submit a source as JSON (base64-encoded bytes).

        Multipart uploads use the same backing handler -- when an SDK
        prefers a single content-type, use this endpoint with the
        body base64-encoded.
        """
        if not payload.content_base64:
            raise ValueError(
                "content_base64 is required; URL-fetched sources are not yet supported"
            )
        try:
            content = base64.b64decode(payload.content_base64)
        except Exception as exc:
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        return await self._commands.send(
            SubmitSourceCommand(
                content=content,
                metadata=payload.metadata,
                filename=payload.filename,
                content_type=payload.content_type,
                kind=payload.kind,
                uri=payload.uri,
            )
        )

    @get_mapping("/{source_id}", tags=["Sources"])
    async def get_source(self, source_id: PathVar[str]) -> SourceRecord:
        """Fetch a single source by id."""
        record = await self._queries.query(GetSourceQuery(source_id=source_id))
        if record is None:
            raise ResourceNotFoundException(f"source {source_id!r} not found")
        return record

    @get_mapping("", tags=["Sources"])
    async def list_sources(
        self,
        status: QueryParam[str] = "",
        kind: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> SourcesPage:
        """Paginated, filterable list of sources."""
        statuses = [SourceStatus(s) for s in _split_csv(status)] if status else []
        kinds = [SourceKind(k) for k in _split_csv(kind)] if kind else []
        return await self._queries.query(
            ListSourcesQuery(statuses=statuses, kinds=kinds, limit=limit, offset=offset)
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]
