# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge graph visualisation service.

Builds the ``KnowledgeGraph`` payload + the ``MermaidGraph`` string
returned by ``GET /api/v1/knowledge:graph``. The graph composes:

* **Knowledge-item nodes** -- one per ``canon_knowledge_items`` row
  matching the filter set. Status / domain / jurisdiction land on
  the node so a UI can colour-code without re-fetching.
* **Source nodes** -- one per ``canon_sources`` row cited by at
  least one rendered knowledge item. Optional.
* **Relation edges** -- ``canon_knowledge_relations`` rows whose
  ends are both in the rendered knowledge set (we don't leak
  edges that point at filtered-out items).
* **Citation edges** -- one ``cites`` edge per knowledge_item ->
  source pair (collapsed across versions and individual chunks --
  the graph view is item-level, not chunk-level).

Two output formats:

* ``json`` (default) -- ``KnowledgeGraph`` with explicit nodes +
  edges lists, ready for D3 / Cytoscape / react-flow.
* ``mermaid`` -- ``MermaidGraph`` carrying a ``graph LR`` block
  that drops directly into a Markdown viewer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from pyfly.container import service
from sqlalchemy import select

from flycanon.interfaces.dtos.graph import (
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    MermaidGraph,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.relation_repository import RelationRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


@service
class KnowledgeGraphService:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        relation_repository: RelationRepository,
        source_repository: SourceRepository,
    ) -> None:
        self._knowledge = knowledge_repository
        self._relations = relation_repository
        self._sources = source_repository

    async def build(
        self,
        *,
        domains: Sequence[Domain] | None = None,
        jurisdictions: Sequence[Jurisdiction] | None = None,
        statuses: Sequence[KnowledgeStatus] | None = None,
        include_sources: bool = True,
        limit: int = 500,
    ) -> KnowledgeGraph:
        """Build the JSON-shaped graph payload."""
        items, _total = await self._knowledge.list_items(
            statuses=[s.value for s in statuses] if statuses else None,
            domains=[d.value for d in domains] if domains else None,
            jurisdictions=[j.value for j in jurisdictions] if jurisdictions else None,
            limit=limit,
            offset=0,
        )
        item_ids = {item.id for item in items}

        nodes: list[GraphNode] = [
            GraphNode(
                id=item.id,
                kind="knowledge_item",
                label=item.title or item.id,
                domain=Domain(item.domain) if item.domain else None,
                jurisdiction=Jurisdiction(item.jurisdiction)
                if item.jurisdiction
                else None,
                status=KnowledgeStatus(item.status) if item.status else None,
                current_version=item.current_version,
            )
            for item in items
        ]

        # Relation edges: only those whose BOTH ends are in the
        # rendered set (don't leak edges that point at filtered-out
        # items).
        relations = await self._relations.list_all(
            from_item_ids=list(item_ids),
        )
        edges: list[GraphEdge] = []
        for rel in relations:
            if rel.to_item_id not in item_ids:
                continue
            edges.append(
                GraphEdge(
                    source=rel.from_item_id,
                    target=rel.to_item_id,
                    kind=rel.kind,
                    label=rel.kind.replace("_", " "),
                    note=rel.note,
                )
            )

        # Source nodes + citation edges. We walk the citation rows
        # for the rendered knowledge versions and collapse them to
        # one edge per (item, source). Chunk-level granularity is
        # available on ``/api/v1/knowledge/{id}/provenance``.
        if include_sources and item_ids:
            citation_pairs = await self._citation_pairs_for_items(items)
            source_ids = {source_id for (_, source_id) in citation_pairs}
            source_rows = (
                await self._sources.get_many(list(source_ids)) if source_ids else []
            )
            sources_by_id = {row.id: row for row in source_rows}
            for source_id in source_ids:
                source = sources_by_id.get(source_id)
                if source is None:
                    continue
                metadata = dict(source.metadata_json or {})
                extracted = metadata.get("extracted") or {}
                label = (
                    metadata.get("title")
                    or extracted.get("title")
                    or source.filename
                    or source.uri
                    or source.id
                )
                nodes.append(
                    GraphNode(
                        id=source.id,
                        kind="source",
                        label=str(label),
                        metadata={
                            "filename": source.filename or "",
                            "uri": source.uri or "",
                            "source_kind": source.kind,
                        },
                    )
                )
            for item_id, source_id in citation_pairs:
                edges.append(
                    GraphEdge(
                        source=item_id,
                        target=source_id,
                        kind="cites",
                        label="cites",
                    )
                )

        return KnowledgeGraph(
            nodes=nodes,
            edges=edges,
            total_nodes=len(nodes),
            total_edges=len(edges),
            rendered_at=datetime.now(UTC).isoformat(),
        )

    async def build_mermaid(
        self,
        *,
        domains: Sequence[Domain] | None = None,
        jurisdictions: Sequence[Jurisdiction] | None = None,
        statuses: Sequence[KnowledgeStatus] | None = None,
        include_sources: bool = True,
        limit: int = 200,
    ) -> MermaidGraph:
        """Build the Mermaid-stringified graph view.

        The graph orientation is ``LR`` (left-to-right) which reads
        well in narrow docs viewers. Node ids are sanitised to a
        Mermaid-safe form (alphanumeric + underscore) so UUIDs land
        cleanly without escaping.
        """
        graph = await self.build(
            domains=domains,
            jurisdictions=jurisdictions,
            statuses=statuses,
            include_sources=include_sources,
            limit=limit,
        )
        lines: list[str] = ["graph LR"]
        for node in graph.nodes:
            label = _escape_mermaid(node.label)
            shape_open, shape_close = ("[", "]") if node.kind == "knowledge_item" else (
                "([",
                "])",
            )
            lines.append(
                f"    {_safe_id(node.id)}{shape_open}\"{label}\"{shape_close}"
            )
        for edge in graph.edges:
            arrow = "-->|" + (edge.label or edge.kind) + "|"
            lines.append(
                f"    {_safe_id(edge.source)} {arrow} {_safe_id(edge.target)}"
            )
        return MermaidGraph(
            mermaid="\n".join(lines),
            total_nodes=graph.total_nodes,
            total_edges=graph.total_edges,
        )

    async def _citation_pairs_for_items(self, items) -> set[tuple[str, str]]:
        """Resolve (item_id, source_id) pairs across rendered items.

        We use the existing ``KnowledgeRepository._session_factory``
        directly for the join because the repo doesn't expose a
        bulk-citation read across multiple items (callers fetch
        per-version today). Keeps the graph build cheap on the
        90% case (~100 items).
        """
        if not items:
            return set()
        item_ids = [item.id for item in items]
        async with self._knowledge._session_factory() as session:  # noqa: SLF001
            stmt = (
                select(
                    KnowledgeVersionRow.knowledge_item_id,
                    CitationRow.source_id,
                )
                .join(
                    CitationRow,
                    CitationRow.knowledge_version_id == KnowledgeVersionRow.id,
                )
                .where(KnowledgeVersionRow.knowledge_item_id.in_(item_ids))
                .distinct()
            )
            rows = (await session.execute(stmt)).all()
        return {(item_id, source_id) for (item_id, source_id) in rows}


def _safe_id(node_id: str) -> str:
    """Coerce an id into a Mermaid-safe identifier.

    Mermaid lexes node ids as ``[A-Za-z0-9_]+``; UUIDs contain dashes
    which the parser rejects mid-statement. We replace dashes with
    underscores and prepend ``n`` so a numeric leading char doesn't
    confuse the lexer.
    """
    return "n" + node_id.replace("-", "_")


def _escape_mermaid(text: str) -> str:
    """Escape characters that confuse Mermaid label parsing."""
    return (
        text.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", " ")
        .replace("\r", " ")
    )
