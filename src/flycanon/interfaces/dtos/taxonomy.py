# Copyright 2026 Firefly Software Solutions Inc
"""Taxonomy DTOs.

flycanon ships a default taxonomy seeded from the workshop personas:
one root node per :class:`Domain` value, plus optional children for
finer-grained scoping (sub-processes, jurisdictions, etc.). Callers
attach custom nodes through ``POST /api/v1/taxonomy/nodes``; the tree
view is rendered breadth-first.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from flycanon.interfaces.enums import Domain


class CreateTaxonomyNodeRequest(BaseModel):
    """Append a new node under an existing parent."""

    parent_id: str | None = Field(
        default=None,
        description="Parent node id. ``None`` creates a new root.",
    )
    slug: str = Field(min_length=1, max_length=128, description="Stable, kebab-case identifier.")
    label: str = Field(min_length=1, max_length=256)
    domain: Domain = Field(description="Domain the node belongs to.")
    description: str | None = Field(default=None, max_length=2000)


class TaxonomyNode(BaseModel):
    """Single node in the taxonomy tree."""

    id: str
    parent_id: str | None
    slug: str
    label: str
    domain: Domain
    description: str | None = Field(default=None)
    depth: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class TaxonomyTree(BaseModel):
    """Breadth-first view of the full taxonomy."""

    nodes: list[TaxonomyNode]
