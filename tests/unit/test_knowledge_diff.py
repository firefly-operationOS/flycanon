# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`KnowledgeDiffService`.

The diff endpoint must produce a structurally-sound diff between
two versions of the same knowledge item -- body unified diff,
per-field scalar changes, and citation set deltas. We exercise the
real SQLite repository (via the test conftest fixtures) so the
join paths get the same treatment a Postgres deploy would.
"""

from __future__ import annotations

import pytest

from flycanon.core.services.knowledge.diff_service import KnowledgeDiffService
from flycanon.core.services.knowledge.errors import (
    KnowledgeItemNotFound,
    KnowledgeVersionNotFound,
)
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow


async def _seed(
    repo,
    *,
    item_id: str,
    version: int,
    body: str,
    title: str = "t",
    summary: str | None = None,
    domain: str = "process",
    jurisdiction: str = "GLOBAL",
    status: str = "published",
    tags: list[str] | None = None,
    citations: list[tuple[str, str | None, int | None]] | None = None,
):
    item = KnowledgeItemRow(
        id=item_id,
        tenant_id="default",
        workspace_id="default",
        status=status,
        current_version=version,
        title=title,
        domain=domain,
        jurisdiction=jurisdiction,
        tags_json=list(tags or []),
    )
    v = KnowledgeVersionRow(
        tenant_id="default",
        workspace_id="default",
        knowledge_item_id=item_id,
        version=version,
        status=status,
        title=title,
        summary=summary,
        body=body,
        domain=domain,
        jurisdiction=jurisdiction,
        tags_json=list(tags or []),
    )
    await repo.upsert_item(item)
    stored = await repo.add_version(v)
    rows = []
    for source_id, chunk_id, page in citations or []:
        rows.append(
            CitationRow(
                tenant_id="default",
                workspace_id="default",
                knowledge_version_id=stored.id,
                chunk_id=chunk_id,
                source_id=source_id,
                page=page,
            )
        )
    if rows:
        await repo.add_citations(rows)
    return stored


@pytest.fixture
def diff_service(repositories):
    return KnowledgeDiffService(repositories["knowledge"])


class TestUnifiedBodyDiff:
    @pytest.mark.asyncio
    async def test_identical_body_returns_empty_diff(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(repo, item_id="k1", version=1, body="hello\nworld")
        await _seed(repo, item_id="k1", version=2, body="hello\nworld")
        diff = await diff_service.diff("k1", 1, 2, tenant_id="default", workspace_id="default")
        assert diff.body_diff == ""

    @pytest.mark.asyncio
    async def test_body_diff_is_unified_format(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(repo, item_id="k1", version=1, body="line1\nline2\nline3")
        await _seed(repo, item_id="k1", version=2, body="line1\nline2-changed\nline3")
        diff = await diff_service.diff("k1", 1, 2, tenant_id="default", workspace_id="default")
        assert "--- version 1" in diff.body_diff
        assert "+++ version 2" in diff.body_diff
        assert "-line2" in diff.body_diff
        assert "+line2-changed" in diff.body_diff


class TestFieldChanges:
    @pytest.mark.asyncio
    async def test_scalar_field_change_reported(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(repo, item_id="k1", version=1, body="x", title="Old title")
        await _seed(repo, item_id="k1", version=2, body="x", title="New title")
        diff = await diff_service.diff("k1", 1, 2, tenant_id="default", workspace_id="default")
        title_change = next(c for c in diff.field_changes if c.field == "title")
        assert title_change.before == "Old title"
        assert title_change.after == "New title"

    @pytest.mark.asyncio
    async def test_tag_set_diff_ignores_order(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(repo, item_id="k1", version=1, body="x", tags=["a", "b", "c"])
        await _seed(repo, item_id="k1", version=2, body="x", tags=["c", "b", "a"])
        diff = await diff_service.diff("k1", 1, 2, tenant_id="default", workspace_id="default")
        # Same set in different order -- no tags change.
        assert not any(c.field == "tags" for c in diff.field_changes)

    @pytest.mark.asyncio
    async def test_tag_addition_reported(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(repo, item_id="k1", version=1, body="x", tags=["a", "b"])
        await _seed(repo, item_id="k1", version=2, body="x", tags=["a", "b", "c"])
        diff = await diff_service.diff("k1", 1, 2, tenant_id="default", workspace_id="default")
        tags_change = next(c for c in diff.field_changes if c.field == "tags")
        assert tags_change.before == ["a", "b"]
        assert tags_change.after == ["a", "b", "c"]


class TestCitationSetDiff:
    @pytest.mark.asyncio
    async def test_added_and_removed_citations_surface(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(
            repo,
            item_id="k1",
            version=1,
            body="x",
            citations=[("src1", "chunk-A", 1), ("src1", "chunk-B", 2)],
        )
        await _seed(
            repo,
            item_id="k1",
            version=2,
            body="x",
            citations=[("src1", "chunk-B", 2), ("src1", "chunk-C", 3)],
        )
        diff = await diff_service.diff("k1", 1, 2, tenant_id="default", workspace_id="default")
        assert [c.chunk_id for c in diff.citations_added] == ["chunk-C"]
        assert [c.chunk_id for c in diff.citations_removed] == ["chunk-A"]
        # The retained citation is in NEITHER list.
        all_keys = {c.chunk_id for c in diff.citations_added + diff.citations_removed}
        assert "chunk-B" not in all_keys


class TestErrors:
    @pytest.mark.asyncio
    async def test_unknown_item_raises(self, diff_service):
        with pytest.raises(KnowledgeItemNotFound):
            await diff_service.diff("does-not-exist", 1, 2, tenant_id="default", workspace_id="default")

    @pytest.mark.asyncio
    async def test_unknown_version_raises(self, repositories, diff_service):
        repo = repositories["knowledge"]
        await _seed(repo, item_id="k1", version=1, body="x")
        with pytest.raises(KnowledgeVersionNotFound):
            await diff_service.diff("k1", 1, 999, tenant_id="default", workspace_id="default")
