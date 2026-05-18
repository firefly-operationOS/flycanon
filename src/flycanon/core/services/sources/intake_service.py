# Copyright 2026 Firefly Software Solutions Inc
"""Source intake orchestrator -- bytes in, indexed source out.

Glue between the loader (chunking), the embedder (vectorising), the
index writer (BM25 + vector projection), and the audit / EDA fanout.
The orchestrator is the only thing controllers + workers should call
when ingesting a new source -- it owns the order-of-writes guarantee.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.embeddings import EmbeddingService
from flycanon.core.services.ingestion import IngestionService
from flycanon.core.services.retrieval import IndexService
from flycanon.interfaces.dtos.source import SourceMetadata, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind, SourceStatus
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


class IntakeService:
    """End-to-end source intake: load -> chunk -> embed -> index."""

    def __init__(
        self,
        *,
        ingestion: IngestionService,
        embeddings: EmbeddingService,
        indexer: IndexService,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        audit: AuditService,
        event_publisher: object | None,
        settings: CanonSettings,
    ) -> None:
        self._ingestion = ingestion
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
        kind = request.kind
        if kind == SourceKind.unknown:
            kind = self._ingestion.detect_kind(filename=filename, content_type=content_type)

        # Pre-allocate the row so the chunk FK + audit subject_id are
        # stable across the whole flow.
        source = SourceRow(
            id=str(uuid.uuid4()),
            kind=kind.value,
            status=SourceStatus.pending.value,
            filename=filename,
            uri=request.uri,
            content_type=content_type,
            content_sha256="",  # populated by the ingestion service
            content_bytes=0,
            metadata_json=_metadata_to_json(request.metadata),
        )

        try:
            result = self._ingestion.ingest(source=source, content=content)
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
            # Persist the embedding_model marker on each chunk row so a
            # later cross-check can detect a model swap.
            updated = []
            for chunk in result.chunks:
                chunk.embedding_model = self._embeddings.model
                updated.append(chunk)
            await self._chunks.replace_for_source(result.source.id, updated)

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
            },
        )
        await self._publish_success(source=result.source, correlation_id=correlation_id)
        logger.info(
            "intake completed id=%s kind=%s n_chunks=%d",
            result.source.id,
            result.source.kind,
            result.source.n_chunks,
        )
        return result.source

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
