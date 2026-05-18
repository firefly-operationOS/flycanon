# Copyright 2026 Firefly Software Solutions Inc
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
