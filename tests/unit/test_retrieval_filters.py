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

"""Coverage for :meth:`RetrievalService._apply_filters`.

The filter pass runs over hits *after* hydration so it can match
against the live knowledge dimensions (status / domain /
jurisdiction / tags) the hydration step stashes on
``hit.metadata`` under ``_knowledge_*``-prefixed keys, with the
source's ``SourceMetadata`` as the fallback when no knowledge
linkage exists.
"""

from __future__ import annotations

from flycanon.core.services.retrieval.retrieval_service import (
    Hit,
    RetrievalFilters,
    RetrievalService,
)


def _hit(
    *,
    chunk_id: str,
    source_id: str = "src-1",
    knowledge_item_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        source_id=source_id,
        knowledge_item_id=knowledge_item_id,
        knowledge_version=1 if knowledge_item_id else None,
        content="...",
        score=0.5,
        bm25_rank=None,
        vector_rank=None,
        metadata=dict(metadata or {}),
    )


def _apply(hits: list[Hit], filters: RetrievalFilters) -> list[Hit]:
    # Bypass __init__ -- ``_apply_filters`` is a pure transformation
    # over its inputs and doesn't touch instance state.
    return list(RetrievalService._apply_filters(None, hits, filters))  # type: ignore[arg-type]


class TestSourceIdFilter:
    def test_keeps_only_matching_source(self):
        hits = [_hit(chunk_id="ch-1", source_id="a"), _hit(chunk_id="ch-2", source_id="b")]
        out = _apply(hits, RetrievalFilters(source_ids=["a"]))
        assert [h.chunk_id for h in out] == ["ch-1"]


class TestKnowledgeItemFilter:
    def test_drops_hits_without_a_live_knowledge_linkage(self):
        hits = [
            _hit(chunk_id="ch-1", knowledge_item_id="k-1"),
            _hit(chunk_id="ch-2", knowledge_item_id=None),
        ]
        out = _apply(hits, RetrievalFilters(knowledge_item_ids=["k-1"]))
        assert [h.chunk_id for h in out] == ["ch-1"]


class TestStatusFilter:
    def test_matches_against_resolved_knowledge_status(self):
        published = _hit(
            chunk_id="ch-1",
            knowledge_item_id="k-1",
            metadata={"_knowledge_status": "published"},
        )
        draft = _hit(
            chunk_id="ch-2",
            knowledge_item_id="k-2",
            metadata={"_knowledge_status": "draft"},
        )
        out = _apply([published, draft], RetrievalFilters(statuses=["published"]))
        assert [h.chunk_id for h in out] == ["ch-1"]


class TestDomainFilter:
    def test_matches_knowledge_domain_first(self):
        h = _hit(
            chunk_id="ch-1",
            metadata={"_knowledge_domain": "compliance", "source_domain": "process"},
        )
        # Knowledge-side wins -- compliance, not the source hint process.
        assert _apply([h], RetrievalFilters(domains=["compliance"])) == [h]
        assert _apply([h], RetrievalFilters(domains=["process"])) == []

    def test_falls_back_to_source_metadata_when_no_knowledge_link(self):
        h = _hit(chunk_id="ch-1", metadata={"source_domain": "process"})
        assert _apply([h], RetrievalFilters(domains=["process"])) == [h]


class TestJurisdictionFilter:
    def test_matches_knowledge_jurisdiction(self):
        h = _hit(chunk_id="ch-1", metadata={"_knowledge_jurisdiction": "EU"})
        assert _apply([h], RetrievalFilters(jurisdictions=["EU"])) == [h]
        assert _apply([h], RetrievalFilters(jurisdictions=["US"])) == []


class TestTagsFilter:
    def test_matches_any_tag_overlap(self):
        # Tags ride as comma-separated strings in the metadata bag
        # because Hit.metadata is dict[str, str] (no list values).
        h = _hit(chunk_id="ch-1", metadata={"_knowledge_tags": "mvp,workshop"})
        # Either tag in the filter should match (set intersection).
        assert _apply([h], RetrievalFilters(tags=["workshop"])) == [h]
        assert _apply([h], RetrievalFilters(tags=["mvp", "other"])) == [h]
        assert _apply([h], RetrievalFilters(tags=["other"])) == []


class TestComposedFilters:
    def test_filters_compose_with_and(self):
        h = _hit(
            chunk_id="ch-1",
            source_id="src-A",
            knowledge_item_id="k-1",
            metadata={
                "_knowledge_status": "published",
                "_knowledge_domain": "process",
            },
        )
        # All three match.
        out = _apply(
            [h],
            RetrievalFilters(
                source_ids=["src-A"],
                knowledge_item_ids=["k-1"],
                statuses=["published"],
                domains=["process"],
            ),
        )
        assert out == [h]
        # Drop on any single mismatch.
        assert _apply([h], RetrievalFilters(source_ids=["src-B"])) == []
        assert _apply([h], RetrievalFilters(statuses=["draft"])) == []
