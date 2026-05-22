# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`KnowledgeGraphService`."""

from __future__ import annotations

import pytest

from flycanon.core.services.knowledge.graph_service import KnowledgeGraphService
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow


@pytest.fixture
def graph_service(repositories):
    return KnowledgeGraphService(
        knowledge_repository=repositories["knowledge"],
        relation_repository=repositories["relation"],
        source_repository=repositories["source"],
    )


async def _seed_item_with_version(
    repo,
    *,
    item_id: str,
    title: str = "t",
    domain: str = "process",
    status: str = "published",
):
    await repo.upsert_item(
        KnowledgeItemRow(
            id=item_id,
            tenant_id="default",
            workspace_id="default",
            status=status,
            current_version=1,
            title=title,
            domain=domain,
            jurisdiction="GLOBAL",
            tags_json=[],
        )
    )
    return await repo.add_version(
        KnowledgeVersionRow(
            tenant_id="default",
            workspace_id="default",
            knowledge_item_id=item_id,
            version=1,
            status=status,
            title=title,
            body="x",
            domain=domain,
            jurisdiction="GLOBAL",
            tags_json=[],
        )
    )


class TestBuildJson:
    @pytest.mark.asyncio
    async def test_renders_items_with_no_relations_or_sources(self, repositories, graph_service):
        await _seed_item_with_version(repositories["knowledge"], item_id="a")
        await _seed_item_with_version(repositories["knowledge"], item_id="b")
        graph = await graph_service.build(include_sources=False)
        ids = {n.id for n in graph.nodes}
        assert ids == {"a", "b"}
        assert graph.edges == []
        assert graph.total_nodes == 2

    @pytest.mark.asyncio
    async def test_relation_edges_only_between_rendered_items(self, repositories, graph_service):
        await _seed_item_with_version(repositories["knowledge"], item_id="a")
        await _seed_item_with_version(repositories["knowledge"], item_id="b")
        await _seed_item_with_version(repositories["knowledge"], item_id="legal", domain="legal")
        # ``a -depends_on-> b`` -- both in process domain.
        await repositories["relation"].add(
            KnowledgeRelationRow(
                tenant_id="default",
                workspace_id="default",
                from_item_id="a",
                to_item_id="b",
                kind="depends_on",
            )
        )
        # ``a -related-> legal`` -- target outside the process filter.
        await repositories["relation"].add(
            KnowledgeRelationRow(
                tenant_id="default",
                workspace_id="default",
                from_item_id="a",
                to_item_id="legal",
                kind="related",
            )
        )
        # Filter to process domain only.
        from flycanon.interfaces.enums import Domain

        graph = await graph_service.build(domains=[Domain.process], include_sources=False)
        # Only the a->b edge survives -- the a->legal edge points
        # outside the rendered set and is suppressed.
        assert [(e.source, e.target, e.kind) for e in graph.edges] == [("a", "b", "depends_on")]

    @pytest.mark.asyncio
    async def test_citation_edges_collapsed_per_source(self, repositories, graph_service):
        version = await _seed_item_with_version(repositories["knowledge"], item_id="a")
        # Seed a source + 2 chunks both cited by the same version -- the
        # graph view should collapse these to ONE cites edge.
        await repositories["source"].add(
            SourceRow(
                id="s1",
                tenant_id="default",
                workspace_id="default",
                kind="markdown",
                status="ingested",
                content_sha256="sha",
                content_bytes=10,
            )
        )
        await repositories["knowledge"].add_citations(
            [
                CitationRow(
                    tenant_id="default",
                    workspace_id="default",
                    knowledge_version_id=version.id,
                    source_id="s1",
                    chunk_id="c1",
                ),
                CitationRow(
                    tenant_id="default",
                    workspace_id="default",
                    knowledge_version_id=version.id,
                    source_id="s1",
                    chunk_id="c2",
                ),
            ]
        )
        graph = await graph_service.build(include_sources=True)
        cites_edges = [e for e in graph.edges if e.kind == "cites"]
        assert len(cites_edges) == 1
        assert cites_edges[0].source == "a"
        assert cites_edges[0].target == "s1"
        # The source landed as a node.
        assert any(n.id == "s1" and n.kind == "source" for n in graph.nodes)


class TestBuildMermaid:
    @pytest.mark.asyncio
    async def test_emits_valid_graph_lr(self, repositories, graph_service):
        await _seed_item_with_version(repositories["knowledge"], item_id="a")
        await _seed_item_with_version(repositories["knowledge"], item_id="b")
        await repositories["relation"].add(
            KnowledgeRelationRow(
                tenant_id="default",
                workspace_id="default",
                from_item_id="a",
                to_item_id="b",
                kind="depends_on",
            )
        )
        m = await graph_service.build_mermaid(include_sources=False)
        # First line is the orientation header.
        assert m.mermaid.splitlines()[0] == "graph LR"
        # Both nodes appear.
        assert "na[" in m.mermaid
        assert "nb[" in m.mermaid
        # The edge between them.
        assert "na -->|depends on| nb" in m.mermaid
