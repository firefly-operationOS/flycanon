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

"""Knowledge graph visualisation DTOs.

Returned by ``GET /api/v1/knowledge:graph``. The default ``json``
format ships a flat ``nodes`` + ``edges`` array consumable by any
graph renderer (D3, Cytoscape, vis-network, react-flow). The
optional ``mermaid`` format returns a stringified Mermaid
``graph LR`` block ready to drop into a Markdown viewer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from flycanon.interfaces.enums import (
    Domain,
    Jurisdiction,
    KnowledgeStatus,
    RelationKind,
)


class GraphNode(BaseModel):
    """One node in the knowledge graph.

    ``kind`` distinguishes the two node families flycanon graphs --
    canonical knowledge items and their source backings. The graph
    viz endpoint includes both when sources are cited by at least
    one rendered knowledge item.
    """

    id: str
    kind: str = Field(
        description="``knowledge_item`` | ``source``.",
        examples=["knowledge_item", "source"],
    )
    label: str = Field(description="Human-readable display label.")
    domain: Domain | None = Field(default=None)
    jurisdiction: Jurisdiction | None = Field(default=None)
    status: KnowledgeStatus | None = Field(default=None)
    current_version: int | None = Field(default=None, ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """One edge in the knowledge graph.

    ``kind`` discriminates between relation edges (between knowledge
    items, carrying a :class:`RelationKind`) and citation edges (from
    a knowledge item to a source row, which read as ``cites``).
    """

    source: str = Field(description="Originating node id.")
    target: str = Field(description="Destination node id.")
    kind: str = Field(
        description=("Either a :class:`RelationKind` value (item -> item) or ``cites`` (item -> source)."),
        examples=["depends_on", "cites"],
    )
    label: str | None = Field(default=None)
    note: str | None = Field(default=None)


class KnowledgeGraph(BaseModel):
    """JSON payload returned by ``/api/v1/knowledge:graph?format=json``."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    total_nodes: int = Field(ge=0)
    total_edges: int = Field(ge=0)
    rendered_at: str = Field(description="ISO-8601 timestamp.")


class MermaidGraph(BaseModel):
    """Stringified Mermaid representation of the graph.

    Returned by ``/api/v1/knowledge:graph?format=mermaid``. The
    string is ready to drop inside a fenced ```mermaid``` block in
    a Markdown document or paste into mermaid.live.
    """

    mermaid: str = Field(description="``graph LR`` Mermaid block. See mermaid.js docs for syntax.")
    total_nodes: int = Field(ge=0)
    total_edges: int = Field(ge=0)


__all__ = [
    "GraphEdge",
    "GraphNode",
    "KnowledgeGraph",
    "MermaidGraph",
    "RelationKind",
]
