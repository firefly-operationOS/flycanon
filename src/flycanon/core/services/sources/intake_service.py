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

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.binary import BinaryNormalizer, NormalizedArtifact
from flycanon.core.services.embeddings import EmbeddingService
from flycanon.core.services.ingestion import IngestionService, LoadedDocument
from flycanon.core.services.ingestion.loaders import LoaderRegistry
from flycanon.core.services.retrieval import IndexService
from flycanon.interfaces.dtos.source import SourceMetadata, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


class IntakeService:
    """End-to-end source intake: normalise -> load -> chunk -> embed -> index."""

    def __init__(
        self,
        *,
        binary_normalizer: BinaryNormalizer,
        ingestion: IngestionService,
        loaders: LoaderRegistry,
        embeddings: EmbeddingService,
        indexer: IndexService,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        audit: AuditService,
        event_publisher: object | None,
        settings: CanonSettings,
    ) -> None:
        self._binary_normalizer = binary_normalizer
        self._ingestion = ingestion
        self._loaders = loaders
        self._embeddings = embeddings
        self._indexer = indexer
        self._sources = source_repository
        self._chunks = chunk_repository
        self._audit = audit
        self._publisher = event_publisher
        self._settings = settings

    async def submit(
        self,
        *,
        request: SubmitSourceRequest,
        content: bytes,
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

        if request.kind != SourceKind.unknown:
            primary_kind = request.kind
        else:
            primary_kind = merged_kind

        source = SourceRow(
            id=str(uuid.uuid4()),
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
            result = self._ingestion.ingest(source=source, content=merged_content)
        except Exception as exc:
            source.status = SourceStatus.failed.value
            source.error_code = getattr(exc, "code", "ingestion_failed")
            source.error_message = str(exc)
            await self._sources.add(source)
            await self._publish_failure(source=source, exc=exc, correlation_id=correlation_id)
            raise

        await self._sources.add(result.source)
        await self._chunks.replace_for_source(result.source.id, result.chunks)

        if result.chunks:
            texts = [chunk.content for chunk in result.chunks]
            embeddings = await self._embeddings.embed(texts)
            await self._indexer.replace_for_source(
                source=result.source,
                chunks=result.chunks,
                embeddings=embeddings,
                embedding_model=self._embeddings.model,
            )
            for chunk in result.chunks:
                chunk.embedding_model = self._embeddings.model
            await self._chunks.replace_for_source(result.source.id, result.chunks)

        await self._audit.record(
            event_type="source.ingested",
            subject_kind="source",
            subject_id=result.source.id,
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
            logger.warning(
                "source.failed publish failed source_id=%s: %s", source.id, inner
            )


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
