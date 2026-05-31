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

"""Knowledge-item relation DTOs.

A relation is a directed, typed edge between two knowledge items.
The semantics are captured by :class:`RelationKind`. Relations
power the provenance graph endpoint plus the knowledge-graph
visualisation endpoint.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from flycanon.interfaces.enums import RelationKind


class CreateRelationRequest(BaseModel):
    """Attach a new relation rooted at the path's ``{item_id}``."""

    to_item_id: str = Field(description="Target knowledge item id.")
    kind: RelationKind = Field(description="Semantic of the edge. See :class:`RelationKind`.")
    since_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Optional knowledge_version of the FROM item the relation "
            "becomes effective from. Lets timelines hide an edge that "
            "wasn't asserted yet at version N."
        ),
    )
    note: str | None = Field(
        default=None,
        max_length=4000,
        description="Free-form note. Surfaced in the graph viz tooltips.",
    )
    actor: str | None = Field(
        default=None,
        description="Stable identifier of the person/service asserting the link.",
    )


class KnowledgeRelation(BaseModel):
    """Public view of a ``canon_knowledge_relations`` row."""

    id: str
    from_item_id: str
    to_item_id: str
    kind: RelationKind
    since_version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None)
    actor: str | None = Field(default=None)
    created_at: datetime


class KnowledgeRelations(BaseModel):
    """Outgoing + incoming relations for a single knowledge item.

    Returned by ``GET /api/v1/knowledge/{id}/relations``. Splits the
    list by direction so a UI can render distinct ``Depends on`` and
    ``Required by`` sections without filtering client-side.
    """

    item_id: str
    outgoing: list[KnowledgeRelation] = Field(default_factory=list)
    incoming: list[KnowledgeRelation] = Field(default_factory=list)
