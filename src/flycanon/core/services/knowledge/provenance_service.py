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

"""Provenance resolver -- citation graph + version history hydration.

Returned by ``GET /api/v1/knowledge/{id}/provenance``. Callers get the
full why-this-is-canonical story without follow-up requests:

* every citation row attached to the requested version,
* compact summaries of the sources the citations point at,
* the full version chain (oldest -> newest) for the item.
"""

from __future__ import annotations

from pyfly.container import service

from flycanon.core.services.knowledge.errors import KnowledgeItemNotFound, KnowledgeVersionNotFound
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.source_repository import SourceRepository


@service
class ProvenanceService:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        source_repository: SourceRepository,
    ) -> None:
        self._knowledge = knowledge_repository
        self._sources = source_repository

    async def resolve(
        self,
        item_id: str,
        version: int | None = None,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> dict:
        """Build the provenance dict for ``(item_id, version)``.

        When ``version`` is omitted, resolves the item's current version.
        ``tenant_id`` / ``workspace_id`` are MANDATORY and threaded
        through every repository lookup so cross-workspace provenance
        leaks are impossible.
        """
        item = await self._knowledge.get_item(
            item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if item is None:
            raise KnowledgeItemNotFound(item_id)
        target_version = version or item.current_version
        version_row = await self._knowledge.get_version(
            item_id,
            target_version,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if version_row is None:
            raise KnowledgeVersionNotFound(item_id, target_version)

        citations = await self._knowledge.list_citations(version_row.id)
        source_ids = sorted({c.source_id for c in citations})
        source_rows: list[SourceRow] = []
        for source_id in source_ids:
            row = await self._sources.get(
                source_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if row is not None:
                source_rows.append(row)

        history = await self._knowledge.list_versions(
            item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

        return {
            "knowledge_item_id": item_id,
            "version": target_version,
            "citations": [_citation_dict(c) for c in citations],
            "sources": [_source_summary(s) for s in source_rows],
            "history": [_version_summary(v) for v in history],
        }


def _citation_dict(row) -> dict:
    return {
        "source_id": row.source_id,
        "chunk_id": row.chunk_id,
        "quote": row.quote,
        "relevance": row.relevance,
        "page": row.page,
    }


def _source_summary(row: SourceRow) -> dict:
    metadata = dict(row.metadata_json or {})
    extracted = dict(metadata.get("extracted") or {})
    return {
        "id": row.id,
        "kind": row.kind,
        # Title falls back to extractor-derived title, then filename, then uri.
        "title": metadata.get("title") or extracted.get("title") or row.filename or row.uri,
        "filename": row.filename,
        "uri": row.uri,
        "content_sha256": row.content_sha256,
        "content_bytes": row.content_bytes,
        "n_chunks": row.n_chunks,
        # Surface the most useful extractor fields directly so callers
        # don't need ``GET /api/v1/sources/{id}`` to render the badge.
        "author": extracted.get("author"),
        "language": extracted.get("language"),
        "page_count": extracted.get("page_count"),
        "word_count": extracted.get("word_count"),
    }


def _version_summary(row: KnowledgeVersionRow) -> dict:
    return {
        "knowledge_item_id": row.knowledge_item_id,
        "version": row.version,
        "status": row.status,
        "title": row.title,
        "summary": row.summary,
        "body": row.body,
        "domain": row.domain,
        "jurisdiction": row.jurisdiction,
        "tags": list(row.tags_json or []),
        "supersedes_version": row.supersedes_version,
        "superseded_by_version": row.superseded_by_version,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "metadata": dict(row.metadata_json or {}),
    }
