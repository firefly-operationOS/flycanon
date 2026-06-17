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

"""Coverage for :meth:`KnowledgeRepository.resolve_source_ids_for_items`.

The whole-document analogue of the chunk-level ``knowledge_item_ids``
retrieval filter: given a set of knowledge item ids it returns the
``source_id`` set cited by **the current version** of those items.
Until now it was only exercised through a fake, so a wrong join column
or a broken version correlation would go unnoticed. Verify it against a
real in-memory SQLite engine (same harness as the sibling
``lookup_published_citations_for_chunks`` test):

* returns the source ids cited by the live (current_version) row;
* excludes citations belonging to superseded versions;
* honours ``tenant_id`` / ``workspace_id`` scoping;
* short-circuits to an empty set on empty input;
* unions multiple sources cited by the requested items.
"""

from __future__ import annotations

import pytest

from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow


async def _seed_version(
    repo,
    *,
    item_id: str,
    version: int,
    source_ids: list[str],
    status: str = "published",
    current_version: int | None = None,
    tenant_id: str = "default",
    workspace_id: str = "default",
) -> KnowledgeVersionRow:
    """Create one item + one version citing ``source_ids`` and return the version.

    The item is (re)written each call; ``current_version`` lets a caller
    point the live pointer at a different version so the older one is
    superseded.
    """
    item = KnowledgeItemRow(
        id=item_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status=status,
        current_version=current_version or version,
        title="t",
        domain="process",
        jurisdiction="GLOBAL",
        tags_json=[],
    )
    v = KnowledgeVersionRow(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        knowledge_item_id=item_id,
        version=version,
        status=status,
        title="t",
        body="b",
        domain="process",
        jurisdiction="GLOBAL",
        tags_json=[],
    )
    await repo.upsert_item(item)
    stored = await repo.add_version(v)
    await repo.add_citations(
        [
            CitationRow(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                knowledge_version_id=stored.id,
                chunk_id=f"ch-{item_id}-{version}-{source_id}",
                source_id=source_id,
            )
            for source_id in source_ids
        ]
    )
    return stored


@pytest.mark.asyncio
async def test_empty_input_short_circuits(repositories):
    result = await repositories["knowledge"].resolve_source_ids_for_items(
        [], tenant_id="default", workspace_id="default"
    )
    assert result == set()


@pytest.mark.asyncio
async def test_resolves_live_version_source_ids(repositories):
    repo = repositories["knowledge"]
    await _seed_version(repo, item_id="k-1", version=1, source_ids=["src-1"])
    result = await repo.resolve_source_ids_for_items(["k-1"], tenant_id="default", workspace_id="default")
    assert result == {"src-1"}


@pytest.mark.asyncio
async def test_unions_multiple_sources_across_items(repositories):
    """Items citing multiple sources union into a single set."""
    repo = repositories["knowledge"]
    await _seed_version(repo, item_id="k-1", version=1, source_ids=["src-1", "src-2"])
    await _seed_version(repo, item_id="k-2", version=1, source_ids=["src-2", "src-3"])
    result = await repo.resolve_source_ids_for_items(
        ["k-1", "k-2"], tenant_id="default", workspace_id="default"
    )
    assert result == {"src-1", "src-2", "src-3"}


@pytest.mark.asyncio
async def test_excludes_superseded_version_citations(repositories):
    """Sources cited only by a superseded version must not surface."""
    repo = repositories["knowledge"]
    # v1 (superseded) cites src-old; item.current_version points at v2.
    await _seed_version(
        repo,
        item_id="k-1",
        version=1,
        source_ids=["src-old"],
        status="superseded",
        current_version=2,
    )
    # v2 (current) cites src-new.
    v2 = KnowledgeVersionRow(
        tenant_id="default",
        workspace_id="default",
        knowledge_item_id="k-1",
        version=2,
        status="published",
        title="t",
        body="b",
        domain="process",
        jurisdiction="GLOBAL",
        tags_json=[],
    )
    stored = await repo.add_version(v2)
    await repo.add_citations(
        [
            CitationRow(
                tenant_id="default",
                workspace_id="default",
                knowledge_version_id=stored.id,
                chunk_id="ch-new",
                source_id="src-new",
            )
        ]
    )
    result = await repo.resolve_source_ids_for_items(["k-1"], tenant_id="default", workspace_id="default")
    assert result == {"src-new"}


@pytest.mark.asyncio
async def test_honours_tenant_and_workspace_scoping(repositories):
    """Citations in another tenant/workspace are not returned."""
    repo = repositories["knowledge"]
    # Three items (one per scope), each citing a distinct source.
    await _seed_version(
        repo,
        item_id="k-default",
        version=1,
        source_ids=["src-default"],
        tenant_id="default",
        workspace_id="default",
    )
    await _seed_version(
        repo,
        item_id="k-other-tenant",
        version=1,
        source_ids=["src-other-tenant"],
        tenant_id="other-tenant",
        workspace_id="default",
    )
    await _seed_version(
        repo,
        item_id="k-other-workspace",
        version=1,
        source_ids=["src-other-workspace"],
        tenant_id="default",
        workspace_id="other-workspace",
    )
    # Ask for all three item ids but scoped to (default, default): only the
    # citation under that scope must come back, even though every item's
    # current version cites a source.
    result = await repo.resolve_source_ids_for_items(
        ["k-default", "k-other-tenant", "k-other-workspace"],
        tenant_id="default",
        workspace_id="default",
    )
    assert result == {"src-default"}
