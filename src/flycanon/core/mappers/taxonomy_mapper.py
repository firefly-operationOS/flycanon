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
