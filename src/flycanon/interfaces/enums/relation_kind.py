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

"""Typed kinds of :class:`KnowledgeRelation`.

Each value carries a directed semantic between two knowledge items.
The set is intentionally small in v1 -- four kinds is enough to
model the canonical operational-knowledge graph; richer ontologies
can land later without breaking the wire shape (extending an enum
is a non-breaking change for consumers that already validate
unknown values).
"""

from __future__ import annotations

from enum import StrEnum


class RelationKind(StrEnum):
    """Semantic kind of a knowledge_item -> knowledge_item edge.

    * ``related`` -- soft "see also" link. Symmetrical in intent
      but stored as a single directed row so the API stays
      consistent with the directional kinds.
    * ``depends_on`` -- ``from`` is only valid while ``to`` is.
      Surfaces in the inbox when ``to`` is updated / retired.
    * ``conflicts_with`` -- ``from`` and ``to`` make
      contradicting canonical claims. Typically populated by the
      conflict-detection background job.
    * ``replaces`` -- ``from`` formally replaces ``to``
      (cross-item supersession; the item-level
      ``superseded_by_item_id`` already exists, ``replaces`` lets
      multiple items collectively replace a legacy one).
    """

    related = "related"
    depends_on = "depends_on"
    conflicts_with = "conflicts_with"
    replaces = "replaces"
