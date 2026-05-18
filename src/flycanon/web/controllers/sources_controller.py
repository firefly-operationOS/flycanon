# Copyright 2026 Firefly Software Solutions Inc
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
    SourceRecord,
    SourcesPage,
    SubmitSourceRequest,
)
from flycanon.interfaces.enums import SourceKind, SourceStatus

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


@rest_controller
@request_mapping("/api/v1/sources")
class SourcesController:
    """REST adapter for the source-intake + read surfaces."""

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @post_mapping("", status_code=201, tags=["Sources"])
    async def submit_json(
        self,
        payload: Valid[Body[SubmitSourceJsonPayload]],
    ) -> SourceRecord:
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

        6. Indexes BM25 (SQLite FTS5) + dense vectors (pgvector by
           default, swappable via ``FLYCANON_VECTOR_STORE``) keyed
           by ``chunk_id``.

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
        """Fetch a single source by id.

        Returns the full :class:`SourceRecord`. The
        ``content_sha256`` field doubles as the dedup anchor: two
        ``SourceRecord`` rows with the same sha point at the same
        canonical content.

        Errors:

        * ``404 source_not_found`` -- the id is unknown.
        """
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
        statuses = [SourceStatus(s) for s in _split_csv(status)] if status else []
        kinds = [SourceKind(k) for k in _split_csv(kind)] if kind else []
        return await self._queries.query(
            ListSourcesQuery(statuses=statuses, kinds=kinds, limit=limit, offset=offset)
        )


def _split_csv(value: str) -> list[str]:
    return [piece.strip() for piece in value.split(",") if piece.strip()]
