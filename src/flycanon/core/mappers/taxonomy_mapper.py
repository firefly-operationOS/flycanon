# Copyright 2026 Firefly Software Solutions Inc
"""``TaxonomyNodeRow`` -> :class:`TaxonomyNode` DTO."""

from __future__ import annotations

from flycanon.interfaces.dtos.taxonomy import TaxonomyNode
from flycanon.interfaces.enums import Domain
from flycanon.models.entities.taxonomy_node import TaxonomyNodeRow


def to_taxonomy_node(row: TaxonomyNodeRow) -> TaxonomyNode:
    return TaxonomyNode(
        id=row.id,
        parent_id=row.parent_id,
        slug=row.slug,
        label=row.label,
        domain=Domain(row.domain),
        description=row.description,
        depth=row.depth,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
