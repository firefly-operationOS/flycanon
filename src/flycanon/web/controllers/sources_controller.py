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

"""Source intake + read endpoints under ``/api/v1/sources``.

The :class:`SourcesController` is the front door for binary intake.
Every supported format (DOCX, XLSX, PPTX, PDF, HTML, Markdown, plain
text, CSV, TSV, JSON, XML, EPUB, RTF, ODT, ODS, ODP, raster images
HEIC/AVIF/TIFF/PNG/JPG/GIF/WebP, archives ZIP/7Z/TAR/GZ, emails
EML/MSG, WebVTT/SRT transcripts) routes through the same handler:

  controller -> SubmitSourceHandler -> IntakeService.submit() ->
  BinaryNormalizer -> SourceLoader -> ParagraphChunker ->
  EmbeddingService -> IndexService -> AuditService + EventPublisher

The endpoint is idempotent on content -- two uploads of the exact
same bytes (post-normalisation) share a :class:`SourceRecord` via
the SHA-256 content hash.
"""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field
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
    put_mapping,
    request_mapping,
)
from starlette.requests import Request

from flycanon.core.services.sources import (
    GetSourceQuery,
    ListSourcesQuery,
    ReplaceSourceCommand,
    SubmitSourceCommand,
)
from flycanon.core.services.sources.async_ingest_service import AsyncIngestService
from flycanon.core.services.sources.url_fetcher import UrlFetcher
from flycanon.interfaces.dtos.job import IngestJob
from flycanon.interfaces.dtos.source import (
    BulkSourceResult,
    BulkSourcesResponse,
    SourceRecord,
    SourcesPage,
    SubmitSourceRequest,
)
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.web.conventions import TenantContext, tenant_context_from_request

logger = logging.getLogger(__name__)


class SubmitSourceJsonPayload(SubmitSourceRequest):
    """Wire payload for ``POST /api/v1/sources``.

    Extends :class:`SubmitSourceRequest` with the ``content_base64``
    field that carries the canonical bytes. We deliberately ship a
    single JSON-only entry point (instead of multipart + JSON) so:

    * SDKs don't have to switch between content types per call.
    * Callers can run the bytes through their own normalisation
      (e.g. signing, encryption) before submission without the
      multipart boundary getting in the way.
    * The OpenAPI spec describes a single, simple shape.

    The base64 string lands as raw bytes server-side; the binary
    normaliser then sniffs the real media type, fans out archives /
    emails, and converts exotic image formats before the loader
    pipeline sees the content.
    """

    content_base64: str | None = None
    filename: str | None = None
    content_type: str | None = None


class BulkSubmitSourcesPayload(BaseModel):
    """Wire payload for ``POST /api/v1/sources:bulk``.

    Carries up to ``MAX_BULK_SOURCES`` source submissions in a
    single request. Each entry is processed independently -- a
    parse / normalisation failure on one entry does not abort the
    batch.
    """

    sources: list[SubmitSourceJsonPayload] = Field(
        min_length=1,
        max_length=100,
        description=(
            "Up to 100 source submissions. Each entry is the same "
            "shape as the JSON body of ``POST /api/v1/sources``."
        ),
    )


@rest_controller
@request_mapping("/api/v1/sources")
class SourcesController:
    """REST adapter for the source-intake + read surfaces."""

    def __init__(
        self,
        commands: DefaultCommandBus,
        queries: DefaultQueryBus,
        url_fetcher: UrlFetcher,
        async_ingest: AsyncIngestService,
    ) -> None:
        self._commands = commands
        self._queries = queries
        self._url_fetcher = url_fetcher
        self._async_ingest = async_ingest

    @post_mapping("", status_code=201)
    async def submit_json(
        self,
        http_request: Request,
        payload: Valid[Body[SubmitSourceJsonPayload]],
        mode: QueryParam[str] = "sync",
        callback_url: QueryParam[str] = "",
    ) -> SourceRecord | IngestJob:
        """Submit a source for intake.

        The request body carries the canonical bytes encoded as
        base64 (``content_base64``) plus the caller-supplied
        metadata. The handler:

        1. Decodes the bytes and runs the binary normaliser. The
           normaliser sniffs the media type from magic bytes, routes
           through the right adapter (image conversion, archive
           expansion, email decomposition, optional Office -> PDF),
           and emits one or more loader-ready artifacts. Archive +
           email intakes are concatenated into a single Markdown
           document with constituent section markers; the ancestry
           chain is preserved on ``metadata.artifacts`` so the audit
           trail can reconstruct it.

        2. Hashes the canonical bytes (SHA-256) -- two uploads of
           the same content share a :class:`SourceRecord`. The hash
           lands on ``content_sha256`` (also unique-indexed in
           Postgres).

        3. Loads through the per-format :class:`SourceLoader`
           registered for the detected kind. Heading-aware loaders
           preserve the section path on every chunk.

        4. Chunks via :class:`ParagraphChunker` (paragraph-bounded,
           token-budgeted, soft overlap).

        5. Embeds every chunk through the configured provider
           (``FLYCANON_EMBEDDING_MODEL``).

        6. Indexes BM25 (Postgres ``tsvector`` + GIN on
           ``canon_chunks``) + dense vectors (pgvector) keyed by
           ``chunk_id``.

        7. Records an audit row and broadcasts ``SourceIngested`` on
           ``flycanon.ingest``.

        Returns the populated :class:`SourceRecord` with ``201
        Created`` on success. Failure modes (all RFC 7807):

        * ``415 unsupported_binary`` -- no normaliser route.
        * ``422 encrypted_pdf`` -- PDF is encrypted.
        * ``422 corrupt_pdf`` / ``corrupt_source`` -- parser failed.
        * ``422 empty_source`` -- loader produced no text.
        * ``413 binary_too_large`` -- exceeds ``FLYCANON_MAX_BYTES``.
        * ``422 binary_fanout_cap_exceeded`` -- archive / email
          expansion exceeded ``FLYCANON_BINARY_MAX_EXPANDED_FILES``.

        Each failure persists a :class:`SourceRecord` with
        ``status=failed`` and the typed ``error_code`` /
        ``error_message`` so callers can inspect and re-submit.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        content, content_type, filename = await self._resolve_payload_content(payload)
        correlation_id = get_correlation_id()
        if mode.lower() == "async":
            from flycanon.core.mappers.job_mapper import to_ingest_job

            row = await self._async_ingest.submit_async(
                request=payload,
                content=content,
                filename=filename,
                content_type=content_type,
                actor=ctx.actor,
                correlation_id=correlation_id,
                callback_url=callback_url or None,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
            return to_ingest_job(row)
        return await self._commands.send(
            SubmitSourceCommand(
                content=content,
                metadata=payload.metadata,
                filename=filename,
                content_type=content_type,
                kind=payload.kind,
                uri=payload.uri,
                actor=ctx.actor,
                correlation_id=correlation_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    async def _resolve_payload_content(
        self,
        payload: SubmitSourceJsonPayload,
    ) -> tuple[bytes, str | None, str | None]:
        """Return ``(content_bytes, content_type, filename)`` for a payload.

        Two modes:

        * ``content_base64`` provided -- decode + return (caller-mode).
        * ``uri`` provided (and no base64) -- fetch the bytes via
          :class:`UrlFetcher` with the size + scheme + timeout
          caps. The content-type from the response headers wins
          over a caller-supplied hint when the latter is absent;
          the filename is derived from the URL's final path
          segment when the caller didn't override it.
        """
        if payload.content_base64:
            try:
                return (
                    base64.b64decode(payload.content_base64),
                    payload.content_type,
                    payload.filename,
                )
            except Exception as exc:
                raise ValueError(f"content_base64 is not valid base64: {exc}") from exc

        if payload.uri:
            fetched = await self._url_fetcher.fetch(payload.uri)
            # The URL's terminal path component is the safest filename
            # default -- ``Manifiesto.pdf`` from
            # ``https://x.com/docs/Manifiesto.pdf?ts=1``.
            url_filename = payload.filename
            if not url_filename:
                from urllib.parse import unquote, urlparse

                terminal = urlparse(fetched.final_url).path.rsplit("/", 1)[-1]
                if terminal:
                    url_filename = unquote(terminal)
            return (
                fetched.content,
                payload.content_type or fetched.content_type,
                url_filename,
            )

        raise ValueError("either content_base64 (inline bytes) or uri (URL to fetch) is required")

    @get_mapping("/{source_id}")
    async def get_source(
        self,
        http_request: Request,
        source_id: PathVar[str],
    ) -> SourceRecord:
        """Fetch a single source by id.

        Returns the full :class:`SourceRecord`. The
        ``content_sha256`` field doubles as the dedup anchor: two
        ``SourceRecord`` rows with the same sha point at the same
        canonical content.

        Errors:

        * ``404 source_not_found`` -- the id is unknown.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        record = await self._queries.query(
            GetSourceQuery(
                source_id=source_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        if record is None:
            raise ResourceNotFoundException(f"source {source_id!r} not found")
        return record

    @post_mapping(":bulk", status_code=200)
    async def submit_bulk(
        self,
        http_request: Request,
        payload: Valid[Body[BulkSubmitSourcesPayload]],
    ) -> BulkSourcesResponse:
        """Submit up to 100 sources in one call.

        Each entry runs through the same intake pipeline as
        :meth:`submit_json` (binary normaliser -> loader -> chunker
        -> embedder -> indexer -> audit + event). Entries are
        processed **sequentially** (we don't fan out internally so
        the binary normaliser's caps remain meaningful per-entry),
        but a failure on any entry never aborts the batch.

        Returns :class:`BulkSourcesResponse` with one
        :class:`BulkSourceResult` per input entry carrying the
        outcome:

        * ``status=succeeded`` -- ``source`` is populated.
        * ``status=failed`` -- ``error_code`` + ``error_message``
          carry the typed failure reason (same codes as the
          single-source endpoint).

        The HTTP status is **always 200** regardless of per-entry
        outcomes; clients inspect each result's ``status`` to
        decide what to retry. The wire shape mirrors the AWS
        SQS / S3 batch-API convention so an SDK retry helper can
        operate on the result list directly.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        correlation_id = get_correlation_id()
        results: list[BulkSourceResult] = []
        succeeded = 0
        failed = 0
        for index, entry in enumerate(payload.sources):
            try:
                if not entry.content_base64:
                    raise ValueError(
                        "content_base64 is required; URL-fetched sources "
                        "are not yet supported in the bulk endpoint"
                    )
                content = base64.b64decode(entry.content_base64)
                source = await self._commands.send(
                    SubmitSourceCommand(
                        content=content,
                        metadata=entry.metadata,
                        filename=entry.filename,
                        content_type=entry.content_type,
                        kind=entry.kind,
                        uri=entry.uri,
                        actor=ctx.actor,
                        correlation_id=correlation_id,
                        tenant_id=ctx.tenant_id,
                        workspace_id=ctx.workspace_id,
                    )
                )
                results.append(BulkSourceResult(index=index, status="succeeded", source=source))
                succeeded += 1
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None) or type(exc).__name__
                results.append(
                    BulkSourceResult(
                        index=index,
                        status="failed",
                        error_code=str(code),
                        error_message=str(exc),
                    )
                )
                failed += 1
                logger.warning(
                    "bulk source entry failed index=%d error=%s message=%s",
                    index,
                    code,
                    exc,
                )
        return BulkSourcesResponse(
            results=results,
            total=len(payload.sources),
            succeeded=succeeded,
            failed=failed,
        )

    @put_mapping("/{source_id}")
    async def replace_source(
        self,
        http_request: Request,
        source_id: PathVar[str],
        payload: Valid[Body[SubmitSourceJsonPayload]],
    ) -> SourceRecord:
        """Re-ingest an existing source under the same ``source_id``.

        Use when the canonical file has been updated and you want
        downstream citations to follow the new content rather than
        orphan to a deleted row. The handler:

        1. Validates the existing ``SourceRow`` exists (returns
           404 ``source_not_found`` otherwise).
        2. Drops the existing chunks + vector projection for the
           row (keeps the row id + audit history).
        3. Runs the same intake pipeline as :meth:`submit_json`
           against the new bytes.
        4. Emits a ``source.replaced`` audit event +
           ``SourceReplaced`` EDA event so downstream projections
           can re-sync.

        Citations pointing at chunks that disappear in the new
        emission raise a ``dangling_citation`` audit warning (per
        citation) but do NOT block the replacement -- the audit
        trail is the system of record for that case.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        if not payload.content_base64:
            raise ValueError("content_base64 is required; URL-fetched re-ingestion is on the roadmap")
        try:
            content = base64.b64decode(payload.content_base64)
        except Exception as exc:
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        return await self._commands.send(
            ReplaceSourceCommand(
                source_id=source_id,
                content=content,
                metadata=payload.metadata,
                filename=payload.filename,
                content_type=payload.content_type,
                kind=payload.kind,
                uri=payload.uri,
                actor=ctx.actor,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )

    @get_mapping("")
    async def list_sources(
        self,
        http_request: Request,
        status: QueryParam[str] = "",
        kind: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> SourcesPage:
        """Paginated, filterable source list.

        Filters compose with ``AND``:

        * ``status`` -- comma-separated lifecycle states
          (``pending``, ``ingesting``, ``ingested``, ``failed``,
          ``superseded``). Empty = any status.
        * ``kind`` -- comma-separated :class:`SourceKind` values.
          Empty = any kind.

        Ordered ``created_at DESC`` (newest first). The compound
        index ``ix_canon_sources_kind_status_created`` keeps the
        query index-only.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        statuses = [SourceStatus(s) for s in _split_csv(status)] if status else []
        kinds = [SourceKind(k) for k in _split_csv(kind)] if kind else []
        return await self._queries.query(
            ListSourcesQuery(
                statuses=statuses,
                kinds=kinds,
                limit=limit,
                offset=offset,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]
