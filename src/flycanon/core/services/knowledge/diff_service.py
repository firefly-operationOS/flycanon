# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge version diff service.

Returned by ``GET /api/v1/knowledge/{id}/diff?from=X&to=Y``. The
shape is a structured JSON document carrying:

* A Unix-style unified body diff (3 lines of context, same form
  ``git diff`` emits).
* Per-field scalar changes (title, summary, domain, jurisdiction,
  tags).
* Citation set deltas (added / removed -- order does not carry
  canonical meaning, so the diff is set-based).
"""

from __future__ import annotations

import difflib

from pyfly.container import service

from flycanon.core.services.knowledge.errors import (
    KnowledgeItemNotFound,
    KnowledgeVersionNotFound,
)
from flycanon.interfaces.dtos.knowledge import (
    Citation,
    FieldChange,
    KnowledgeVersionDiff,
)
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository


@service
class KnowledgeDiffService:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self._repository = repository

    async def diff(
        self,
        item_id: str,
        from_version: int,
        to_version: int,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> KnowledgeVersionDiff:
        """Diff two versions of a knowledge item, scoped to ``(tenant, workspace)``.

        Scope kwargs are MANDATORY. A cross-workspace diff raises
        :class:`KnowledgeItemNotFound`.
        """
        item = await self._repository.get_item(
            item_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if item is None:
            raise KnowledgeItemNotFound(item_id)

        # Resolve both versions; raise if either is missing -- the
        # caller almost certainly mistyped a version number.
        v_from = await self._repository.get_version(
            item_id,
            from_version,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if v_from is None:
            raise KnowledgeVersionNotFound(item_id, from_version)
        v_to = await self._repository.get_version(
            item_id,
            to_version,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if v_to is None:
            raise KnowledgeVersionNotFound(item_id, to_version)

        body_diff = _unified_body_diff(v_from, v_to)
        field_changes = _scalar_field_changes(v_from, v_to)

        citations_from = await self._repository.list_citations(v_from.id)
        citations_to = await self._repository.list_citations(v_to.id)
        added, removed = _citation_set_diff(citations_from, citations_to)

        return KnowledgeVersionDiff(
            knowledge_item_id=item_id,
            from_version=from_version,
            to_version=to_version,
            body_diff=body_diff,
            field_changes=field_changes,
            citations_added=added,
            citations_removed=removed,
        )


def _unified_body_diff(
    v_from: KnowledgeVersionRow,
    v_to: KnowledgeVersionRow,
) -> str:
    """Return a ``git diff``-style unified diff of the two bodies."""
    before_lines = (v_from.body or "").splitlines(keepends=False)
    after_lines = (v_to.body or "").splitlines(keepends=False)
    if before_lines == after_lines:
        return ""
    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"version {v_from.version}",
        tofile=f"version {v_to.version}",
        lineterm="",
        n=3,
    )
    return "\n".join(diff_lines)


def _scalar_field_changes(
    v_from: KnowledgeVersionRow,
    v_to: KnowledgeVersionRow,
) -> list[FieldChange]:
    """Compare the small fixed set of scalar columns."""
    changes: list[FieldChange] = []
    fields = (
        ("title", v_from.title, v_to.title),
        ("summary", v_from.summary, v_to.summary),
        ("domain", v_from.domain, v_to.domain),
        ("jurisdiction", v_from.jurisdiction, v_to.jurisdiction),
        ("status", v_from.status, v_to.status),
    )
    for name, before, after in fields:
        if before != after:
            changes.append(FieldChange(field=name, before=before, after=after))

    # Tags are list-typed so we compare as sets to ignore order.
    before_tags = set(v_from.tags_json or [])
    after_tags = set(v_to.tags_json or [])
    if before_tags != after_tags:
        changes.append(
            FieldChange(
                field="tags",
                before=sorted(before_tags),
                after=sorted(after_tags),
            )
        )
    return changes


def _citation_set_diff(before, after) -> tuple[list[Citation], list[Citation]]:
    """Compare two citation lists as sets keyed by (source_id, chunk_id).

    Citation order does not carry canonical meaning -- two versions
    citing the same evidence in a different order are NOT a "change"
    in any meaningful sense. The set-based diff surfaces only the
    genuine adds + removes.
    """

    def _key(row) -> tuple[str, str | None]:
        return (row.source_id, row.chunk_id)

    before_index = {_key(c): c for c in before}
    after_index = {_key(c): c for c in after}
    added_keys = set(after_index) - set(before_index)
    removed_keys = set(before_index) - set(after_index)

    added = [_to_dto(after_index[k]) for k in sorted(added_keys)]
    removed = [_to_dto(before_index[k]) for k in sorted(removed_keys)]
    return added, removed


def _to_dto(row) -> Citation:
    return Citation(
        source_id=row.source_id,
        chunk_id=row.chunk_id,
        quote=row.quote,
        relevance=row.relevance,
        page=row.page,
    )
