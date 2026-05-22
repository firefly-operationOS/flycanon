# Copyright 2026 Firefly Software Solutions Inc
"""Source intake orchestrator -- bytes in, indexed source out.

End-to-end pipeline that controllers + workers call when ingesting a
new binary. The orchestrator owns:

1. **Binary normalisation** -- :class:`BinaryNormalizer` sniffs the
   actual media type from magic bytes, routes through the right
   adapter (image conversion, archive expansion, email
   decomposition, optional Office conversion), and emits one or more
   :class:`NormalizedArtifact` rows.

2. **Loading** -- each artifact is fed to the loader registered for
   its kind, producing a :class:`LoadedDocument` with heading-anchored
   sections. Multi-artifact intakes (archives, emails) are merged
   into a single document with constituent section markers so the
   retrieval index sees one logical source per intake call.

3. **Chunking** -- :class:`ParagraphChunker` packs paragraphs into
   token-budgeted chunks with soft overlap.

4. **Embedding** -- :class:`EmbeddingService` produces one vector
   per chunk in batched, retried, cost-tracked calls.

5. **Indexing** -- :class:`IndexService` writes the BM25 + dense
   projections to the corpus + vector store, keyed by ``chunk_id``
   so retrieval-time RRF fusion lines up.

6. **Audit + EDA** -- :class:`AuditService` records the mutation and
   the event publisher broadcasts ``SourceIngested`` on
   ``flycanon.ingest``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pyfly.container import service
from pyfly.eda import EventPublisher
from sqlalchemy.exc import IntegrityError

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.binary import BinaryNormalizer, NormalizedArtifact
from flycanon.core.services.embeddings import EmbeddingService
from flycanon.core.services.ingestion import IngestionService, LoadedDocument
from flycanon.core.services.ingestion.loaders import LoaderRegistry
from flycanon.core.services.metadata import MetadataExtractor
from flycanon.core.services.pii import (
    PiiPolicy,
    PiiPolicyViolation,
    PiiScanner,
    build_pii_scanner,
)
from flycanon.core.services.pii.scanner import redact as pii_redact
from flycanon.core.services.retrieval import IndexService
from flycanon.interfaces.dtos.source import SourceMetadata, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


@service
class IntakeService:
    """End-to-end source intake: normalise -> load -> chunk -> embed -> index."""

    def __init__(
        self,
        binary_normalizer: BinaryNormalizer,
        ingestion: IngestionService,
        loaders: LoaderRegistry,
        embeddings: EmbeddingService,
        indexer: IndexService,
        metadata_extractor: MetadataExtractor,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        audit: AuditService,
        event_publisher: EventPublisher,
        settings: CanonSettings,
    ) -> None:
        self._binary_normalizer = binary_normalizer
        self._ingestion = ingestion
        self._loaders = loaders
        self._embeddings = embeddings
        self._indexer = indexer
        self._metadata = metadata_extractor
        self._sources = source_repository
        self._chunks = chunk_repository
        self._audit = audit
        self._publisher = event_publisher
        self._settings = settings
        # Resolve the configured PII scanner once at construction.
        # ``None`` when the scanner is disabled (or the policy is
        # ``disabled``) -- the submit path short-circuits the check.
        try:
            policy = PiiPolicy(settings.pii_policy)
        except ValueError:
            policy = PiiPolicy.warn
        self._pii_policy = policy
        self._pii_scanner: PiiScanner | None = (
            build_pii_scanner(settings.pii_scanner) if policy != PiiPolicy.disabled else None
        )

    async def submit(
        self,
        *,
        request: SubmitSourceRequest,
        content: bytes,
        tenant_id: str,
        workspace_id: str,
        filename: str | None = None,
        content_type: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> SourceRow:
        """Run the full intake pipeline and return the populated row."""
        artifacts = await self._binary_normalizer.normalise(
            content,
            declared_media_type=content_type,
            filename=filename,
        )
        merged_kind, merged_content, merged_metadata = self._merge_artifacts(artifacts, filename)
        merged_content, merged_metadata = self._apply_pii_policy(merged_content, merged_metadata)

        primary_kind = request.kind if request.kind != SourceKind.unknown else merged_kind

        source = SourceRow(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            kind=primary_kind.value,
            status=SourceStatus.pending.value,
            filename=filename,
            uri=request.uri,
            content_type=content_type,
            content_sha256="",
            content_bytes=0,
            metadata_json={**_metadata_to_json(request.metadata), **merged_metadata},
        )

        try:
            result = self._ingestion.ingest(
                source=source,
                content=merged_content,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        except Exception as exc:
            source.status = SourceStatus.failed.value
            source.error_code = getattr(exc, "code", "ingestion_failed")
            source.error_message = str(exc)
            await self._sources.add(source)
            await self._publish_failure(source=source, exc=exc, correlation_id=correlation_id)
            raise

        # Per-format metadata extraction. We feed the loader-produced
        # text back into the language detector so the ``language``
        # field is populated even when the embedded metadata doesn't
        # carry one. The extracted dict lands under
        # ``metadata_json.extracted`` so the source schema stays
        # stable while still exposing the rich facets.
        primary_artifact = artifacts[0] if artifacts else None
        primary_media = primary_artifact.media_type if primary_artifact else (content_type or "")
        primary_bytes = primary_artifact.bytes if primary_artifact else merged_content
        text_sample = self._first_text(result.chunks)
        extracted = self._metadata.extract(
            primary_bytes,
            media_type=primary_media,
            filename=filename,
            text_sample=text_sample,
        )
        metadata = dict(result.source.metadata_json or {})
        metadata["extracted"] = extracted.to_dict()
        result.source.metadata_json = metadata

        # Idempotent on the SHA-256: the same bytes resolve to the
        # same row, no matter how many parallel callers submit them.
        # We pre-check + handle IntegrityError so the race window
        # between the SELECT and the INSERT is also covered (without
        # the recovery branch, the second caller would see a 500
        # IntegrityError from the partial-unique index on
        # ``content_sha256``).
        existing = await self._sources.get_by_content_sha256(result.source.content_sha256)
        if existing is not None:
            logger.info(
                "intake idempotent: content_sha256=%s already mapped to source_id=%s",
                result.source.content_sha256,
                existing.id,
            )
            return existing
        try:
            await self._sources.add(result.source)
        except IntegrityError:
            existing = await self._sources.get_by_content_sha256(result.source.content_sha256)
            if existing is not None:
                logger.info(
                    "intake idempotent (race-resolved): content_sha256=%s -> source_id=%s",
                    result.source.content_sha256,
                    existing.id,
                )
                return existing
            raise

        if result.chunks:
            texts = [chunk.content for chunk in result.chunks]
            embeddings = await self._embeddings.embed(texts)
            # Stamp the model on the chunks BEFORE persisting so the
            # canon_chunks row carries the right ``embedding_model``
            # column on its single INSERT. The vector projection then
            # mirrors the same chunk ids into the chosen vector store
            # for retrieval-time RRF fusion.
            for chunk in result.chunks:
                chunk.embedding_model = self._embeddings.model
            await self._chunks.replace_for_source(result.source.id, result.chunks)
            await self._indexer.replace_for_source(
                source=result.source,
                chunks=result.chunks,
                embeddings=embeddings,
                embedding_model=self._embeddings.model,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        else:
            await self._chunks.replace_for_source(result.source.id, [])

        await self._audit.record(
            event_type="source.ingested",
            subject_kind="source",
            subject_id=result.source.id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "kind": result.source.kind,
                "content_sha256": result.source.content_sha256,
                "n_chunks": result.source.n_chunks,
                "n_artifacts": len(artifacts),
            },
        )
        await self._publish_success(source=result.source, correlation_id=correlation_id)
        logger.info(
            "intake completed id=%s kind=%s n_chunks=%d n_artifacts=%d",
            result.source.id,
            result.source.kind,
            result.source.n_chunks,
            len(artifacts),
        )
        return result.source

    async def replace(
        self,
        *,
        source_id: str,
        request: SubmitSourceRequest,
        content: bytes,
        tenant_id: str,
        workspace_id: str,
        filename: str | None = None,
        content_type: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> SourceRow:
        """Re-ingest an existing source in place, preserving the row id.

        Use when a canonical file is updated and downstream citations
        should follow the new content rather than orphan to the deleted
        row. The flow:

        1. Look up the existing :class:`SourceRow` -- 404 propagates
           up if it's missing.
        2. Normalise + load + chunk + embed the new bytes through the
           same pipeline as :meth:`submit`.
        3. Drop the existing chunks + their vector projection via
           :meth:`ChunkRepository.replace_for_source` /
           :meth:`IndexService.replace_for_source` (idempotent --
           same calls the submit path makes).
        4. UPDATE the existing source row's mutable fields
           (filename, uri, content_sha256, content_bytes, n_chunks,
           metadata_json) instead of inserting a new row.
        5. Audit ``source.replaced`` + publish a ``SourceReplaced``
           EDA event so downstream projections can re-sync.

        Citations referencing chunks that disappear in the new
        emission are not deleted -- a future pass surfaces them as
        ``dangling_citation`` audit warnings. v1 simply preserves
        the citation rows (their ``chunk_id`` will resolve to
        ``None`` in the provenance graph; out of scope for this
        commit to repair).
        """
        existing = await self._sources.get(
            source_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if existing is None:
            from flycanon.core.services.sources.errors import SourceNotFound

            raise SourceNotFound(source_id)

        artifacts = await self._binary_normalizer.normalise(
            content,
            declared_media_type=content_type,
            filename=filename,
        )
        merged_kind, merged_content, merged_metadata = self._merge_artifacts(artifacts, filename)
        # PII guardrail runs on the re-ingest path too: a clean v1 of
        # a file may be replaced with a v2 that introduces emails /
        # SSNs / etc., and the policy must catch that just like
        # initial submit. Same scanner + policy resolution as submit().
        merged_content, merged_metadata = self._apply_pii_policy(merged_content, merged_metadata)
        primary_kind = request.kind if request.kind != SourceKind.unknown else merged_kind

        # Build a transient SourceRow carrying the NEW values so the
        # ingestion pipeline (which already knows how to chunk +
        # compute content_sha256 + emit chunks for a row) does its
        # work; we then transplant the result onto the EXISTING row
        # (preserving its id + created_at + audit history).
        scratch = SourceRow(
            id=existing.id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            kind=primary_kind.value,
            status=SourceStatus.pending.value,
            filename=filename or existing.filename,
            uri=request.uri or existing.uri,
            content_type=content_type or existing.content_type,
            content_sha256="",
            content_bytes=0,
            metadata_json={
                **(existing.metadata_json or {}),
                **_metadata_to_json(request.metadata),
                **merged_metadata,
            },
        )
        result = self._ingestion.ingest(
            source=scratch,
            content=merged_content,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

        # Per-format metadata extraction (identical to submit()).
        primary_artifact = artifacts[0] if artifacts else None
        primary_media = primary_artifact.media_type if primary_artifact else (content_type or "")
        primary_bytes = primary_artifact.bytes if primary_artifact else merged_content
        text_sample = self._first_text(result.chunks)
        extracted = self._metadata.extract(
            primary_bytes,
            media_type=primary_media,
            filename=filename,
            text_sample=text_sample,
        )
        metadata = dict(result.source.metadata_json or {})
        metadata["extracted"] = extracted.to_dict()

        # Now flip the existing row in place with the new content
        # (preserves id, created_at, ingested_at history).
        existing.kind = result.source.kind
        existing.status = SourceStatus.ingested.value
        existing.filename = scratch.filename
        existing.uri = scratch.uri
        existing.content_type = scratch.content_type
        existing.content_sha256 = result.source.content_sha256
        existing.content_bytes = result.source.content_bytes
        existing.n_chunks = result.source.n_chunks
        existing.metadata_json = metadata
        existing.error_code = None
        existing.error_message = None
        updated_row = await self._sources.update(existing)

        if result.chunks:
            texts = [chunk.content for chunk in result.chunks]
            embeddings = await self._embeddings.embed(texts)
            for chunk in result.chunks:
                chunk.embedding_model = self._embeddings.model
                chunk.source_id = updated_row.id
            await self._chunks.replace_for_source(updated_row.id, result.chunks)
            await self._indexer.replace_for_source(
                source=updated_row,
                chunks=result.chunks,
                embeddings=embeddings,
                embedding_model=self._embeddings.model,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        else:
            await self._chunks.replace_for_source(updated_row.id, [])

        await self._audit.record(
            event_type="source.replaced",
            subject_kind="source",
            subject_id=updated_row.id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "kind": updated_row.kind,
                "content_sha256": updated_row.content_sha256,
                "n_chunks": updated_row.n_chunks,
                "n_artifacts": len(artifacts),
            },
        )
        if self._publisher is not None:
            try:
                await self._publisher.publish(  # type: ignore[attr-defined]
                    destination=self._settings.ingest_topic,
                    event_type="SourceReplaced",
                    payload={
                        "source_id": updated_row.id,
                        "kind": updated_row.kind,
                        "content_sha256": updated_row.content_sha256,
                        "n_chunks": updated_row.n_chunks,
                    },
                    headers={"correlation-id": correlation_id} if correlation_id else None,
                )
            except Exception as exc:
                logger.warning(
                    "SourceReplaced publish failed source_id=%s: %s",
                    updated_row.id,
                    exc,
                )
        logger.info(
            "intake replaced id=%s kind=%s n_chunks=%d",
            updated_row.id,
            updated_row.kind,
            updated_row.n_chunks,
        )
        return updated_row

    def _apply_pii_policy(
        self,
        content: bytes,
        metadata: dict[str, Any],
    ) -> tuple[bytes, dict[str, Any]]:
        """Run the configured PII scan against ``content`` and apply the policy.

        Returns ``(possibly-redacted-content, metadata-with-findings)``.
        The scanner operates on the merged text (decoded UTF-8) so a
        contaminated child artefact in an archive triggers the same
        policy as a top-level hit. Raises :class:`PiiPolicyViolation`
        when the policy is ``reject`` and findings are non-empty.
        """
        if self._pii_scanner is None or self._pii_policy == PiiPolicy.disabled:
            return content, metadata
        text_view = self._decode_for_scan(content)
        findings = self._pii_scanner.scan(text_view)
        if not findings:
            return content, metadata
        if self._pii_policy == PiiPolicy.reject:
            raise PiiPolicyViolation(findings)
        if self._pii_policy == PiiPolicy.redact:
            redacted = pii_redact(text_view, findings)
            content = redacted.encode("utf-8")
        metadata.setdefault(
            "pii_findings",
            [{"kind": f.kind, "start": f.start, "end": f.end} for f in findings],
        )
        return content, metadata

    @staticmethod
    def _decode_for_scan(content: bytes) -> str:
        """Best-effort UTF-8 decode for the PII scanner.

        Binary payloads (PDFs in their canonical form, images,
        archives that didn't get expanded inline) decode as
        ``replace`` -- the scanner runs against the parts of the
        stream that ARE text, and the binary spans become
        unmatchable byte runs.
        """
        try:
            return content.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return content.decode("utf-8", errors="replace") if isinstance(content, bytes) else ""

    @staticmethod
    def _first_text(chunks: list[Any]) -> str:
        """Return up to ~2 000 chars of the first ingested chunk's
        text so the language detector has something to work with.
        """
        if not chunks:
            return ""
        body = getattr(chunks[0], "content", "") or ""
        return body[:2000]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _merge_artifacts(
        self,
        artifacts: list[NormalizedArtifact],
        original_filename: str | None,
    ) -> tuple[SourceKind, bytes, dict[str, Any]]:
        """Collapse a fan-out artifact list into a single content payload.

        Single-artifact intakes pass through unchanged. Multi-artifact
        intakes (archives, emails with attachments) get merged into
        one Markdown document with ``## Artifact: <filename>`` section
        markers separating constituents -- the chunker treats each
        constituent as a sibling section, and the citation graph
        records the ancestry on the source row's metadata.
        """
        if len(artifacts) == 1:
            artifact = artifacts[0]
            return artifact.kind, artifact.bytes, _ancestry_metadata([artifact])

        merged_sections: list[str] = []
        kind = SourceKind.archive
        for index, artifact in enumerate(artifacts):
            loader = self._loaders.get(artifact.kind)
            if loader is None:
                logger.warning(
                    "intake skipping artifact kind=%s filename=%s -- no loader",
                    artifact.kind.value,
                    artifact.filename,
                )
                continue
            try:
                document = loader.load(artifact.bytes, filename=artifact.filename)
            except Exception as exc:
                logger.warning(
                    "intake skipping artifact kind=%s filename=%s -- loader failed: %s",
                    artifact.kind.value,
                    artifact.filename,
                    exc,
                )
                continue
            rendered = _render_document(document, artifact_label=artifact.filename, index=index)
            if rendered:
                merged_sections.append(rendered)

        merged = "\n\n".join(merged_sections).encode("utf-8")
        return kind, merged, _ancestry_metadata(artifacts)

    async def _publish_success(self, *, source: SourceRow, correlation_id: str | None) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.ingest_topic,
                event_type=self._settings.source_ingested_event,
                payload={
                    "source_id": source.id,
                    "kind": source.kind,
                    "content_sha256": source.content_sha256,
                    "n_chunks": source.n_chunks,
                },
                headers={"correlation-id": correlation_id} if correlation_id else None,
            )
        except Exception as exc:
            logger.warning("source.ingested publish failed source_id=%s: %s", source.id, exc)

    async def _publish_failure(
        self,
        *,
        source: SourceRow,
        exc: BaseException,
        correlation_id: str | None,
    ) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.ingest_topic,
                event_type=self._settings.source_ingestion_failed_event,
                payload={
                    "source_id": source.id,
                    "kind": source.kind,
                    "code": getattr(exc, "code", "ingestion_failed"),
                    "message": str(exc),
                },
                headers={"correlation-id": correlation_id} if correlation_id else None,
            )
        except Exception as inner:
            logger.warning("source.failed publish failed source_id=%s: %s", source.id, inner)


def _metadata_to_json(metadata: SourceMetadata) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if metadata.title:
        payload["title"] = metadata.title
    if metadata.author:
        payload["author"] = metadata.author
    if metadata.domain is not None:
        payload["domain"] = metadata.domain.value
    if metadata.jurisdiction is not None:
        payload["jurisdiction"] = metadata.jurisdiction.value
    if metadata.language:
        payload["language"] = metadata.language
    if metadata.tags:
        payload["tags"] = list(metadata.tags)
    if metadata.extra:
        payload.update(metadata.extra)
    return payload


def _ancestry_metadata(artifacts: list[NormalizedArtifact]) -> dict[str, Any]:
    return {
        "artifacts": [
            {
                "filename": artifact.filename,
                "kind": artifact.kind.value,
                "media_type": artifact.media_type,
                "derived_from": list(artifact.derived_from),
            }
            for artifact in artifacts
        ]
    }


def _render_document(
    document: LoadedDocument,
    *,
    artifact_label: str,
    index: int,
) -> str:
    if not document.sections:
        if document.raw_text:
            return f"## Artifact: {artifact_label}\n\n{document.raw_text.strip()}"
        return ""
    rendered_sections: list[str] = [f"## Artifact: {artifact_label}"]
    if document.title:
        rendered_sections.append(f"### {document.title}")
    for section in document.sections:
        if section.path:
            for level, heading in enumerate(section.path, start=3):
                rendered_sections.append(f"{'#' * min(level, 6)} {heading}")
        rendered_sections.append(section.body.strip())
    return "\n\n".join(rendered_sections)
