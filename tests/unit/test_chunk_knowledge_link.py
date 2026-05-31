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

"""Coverage for :meth:`KnowledgeRepository.lookup_published_citations_for_chunks`.

The lookup is the seam the retrieval service uses to populate
``Hit.knowledge_item_id`` / ``Hit.knowledge_version`` and to power
the knowledge-side post-retrieval filters. Verify it:

* returns the live (current_version) row when a chunk is cited;
* ignores superseded historical citations;
* tolerates an empty input;
* returns the full knowledge dimensions (domain, jurisdiction, tags)
  so the retrieval filters can match against them without a second
  round-trip.
"""

from __future__ import annotations

import pytest

from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow


async def _seed_item_with_citation(
    repo,
    *,
    item_id: str,
    version: int,
    chunk_id: str,
    status: str = "published",
    domain: str = "process",
    jurisdiction: str = "GLOBAL",
    tags: list[str] | None = None,
    current_version: int | None = None,
) -> KnowledgeVersionRow:
    item = KnowledgeItemRow(
        id=item_id,
        tenant_id="default",
        workspace_id="default",
        status=status,
        current_version=current_version or version,
        title="t",
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
        title="t",
        body="b",
        domain=domain,
        jurisdiction=jurisdiction,
        tags_json=list(tags or []),
    )
    await repo.upsert_item(item)
    stored = await repo.add_version(v)
    await repo.add_citations(
        [
            CitationRow(
                tenant_id="default",
                workspace_id="default",
                knowledge_version_id=stored.id,
                chunk_id=chunk_id,
                source_id="src-1",
            )
        ]
    )
    return stored


class TestLookupPublishedCitationsForChunks:
    @pytest.mark.asyncio
    async def test_empty_input_short_circuits(self, repositories):
        result = await repositories["knowledge"].lookup_published_citations_for_chunks([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_resolves_live_version_when_chunk_cited(self, repositories):
        repo = repositories["knowledge"]
        await _seed_item_with_citation(
            repo,
            item_id="k-1",
            version=1,
            chunk_id="ch-1",
            domain="compliance",
            jurisdiction="EU",
            tags=["mvp", "workshop"],
        )
        result = await repo.lookup_published_citations_for_chunks(["ch-1"])
        link = result["ch-1"]
        assert link.item_id == "k-1"
        assert link.version == 1
        assert link.status == "published"
        assert link.domain == "compliance"
        assert link.jurisdiction == "EU"
        assert set(link.tags) == {"mvp", "workshop"}

    @pytest.mark.asyncio
    async def test_ignores_superseded_versions(self, repositories):
        """A chunk cited only by v1 must not surface as live when v2 is current."""
        repo = repositories["knowledge"]
        # v1 cites ch-old, status=superseded, item.current_version=2.
        await _seed_item_with_citation(
            repo,
            item_id="k-1",
            version=1,
            chunk_id="ch-old",
            status="superseded",
            current_version=2,
        )
        # v2 (current) cites a different chunk.
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
                    source_id="src-1",
                )
            ]
        )
        result = await repo.lookup_published_citations_for_chunks(["ch-old", "ch-new"])
        # Only the live citation resolves.
        assert "ch-old" not in result
        assert result["ch-new"].version == 2

    @pytest.mark.asyncio
    async def test_unlinked_chunks_omitted(self, repositories):
        """Chunks not cited by any knowledge_version are absent from the result."""
        repo = repositories["knowledge"]
        await _seed_item_with_citation(repo, item_id="k-1", version=1, chunk_id="ch-1")
        result = await repo.lookup_published_citations_for_chunks(["ch-1", "ch-orphan"])
        assert "ch-1" in result
        assert "ch-orphan" not in result
