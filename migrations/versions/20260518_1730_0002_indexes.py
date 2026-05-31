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

"""Compound indexes for production query patterns.

Revision ID: 0002_indexes
Revises: 0001_init
Create Date: 2026-05-18 17:30:00 UTC

Adds compound indexes that match the read paths the controllers and
projections actually execute. Each index is justified by the query
that motivates it:

* ``canon_sources(kind, status, created_at DESC)``     -- list view
  (``GET /api/v1/sources?kind=...&status=...``).
* ``canon_chunks(source_id, embedding_model)``         -- re-embedding
  detection (chunks whose embedding model drifts from settings).
* ``canon_knowledge_items(domain, jurisdiction, status)``
  -- canonical-list view filtered by every dimension at once.
* ``canon_knowledge_items(updated_at DESC)``           -- "most-recent"
  list view ordering.
* ``canon_knowledge_versions(knowledge_item_id, status)``
  -- history view filtered by status.
* ``canon_candidates(source_id, status, created_at DESC)``
  -- pending-proposal queue per source.
* ``canon_audit_events(event_type, occurred_at DESC)`` -- chronological
  audit feed filtered by type.
* ``canon_audit_events(actor, occurred_at DESC)``      -- per-actor
  audit trail.
* ``canon_citations(source_id)``                       -- already
  present as a single-column index; this revision is a no-op for it.

The migration is index-only; no schema changes. Downgrade drops the
new indexes.
"""

from __future__ import annotations

from alembic import op

revision = "0002_indexes"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_canon_sources_kind_status_created",
        "canon_sources",
        ["kind", "status", "created_at"],
    )
    op.create_index(
        "ix_canon_chunks_source_model",
        "canon_chunks",
        ["source_id", "embedding_model"],
    )
    op.create_index(
        "ix_canon_knowledge_items_domain_jurisdiction_status",
        "canon_knowledge_items",
        ["domain", "jurisdiction", "status"],
    )
    op.create_index(
        "ix_canon_knowledge_items_updated_at",
        "canon_knowledge_items",
        ["updated_at"],
    )
    op.create_index(
        "ix_canon_knowledge_versions_item_status",
        "canon_knowledge_versions",
        ["knowledge_item_id", "status"],
    )
    op.create_index(
        "ix_canon_candidates_source_status_created",
        "canon_candidates",
        ["source_id", "status", "created_at"],
    )
    op.create_index(
        "ix_canon_audit_events_type_occurred",
        "canon_audit_events",
        ["event_type", "occurred_at"],
    )
    op.create_index(
        "ix_canon_audit_events_actor_occurred",
        "canon_audit_events",
        ["actor", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_canon_audit_events_actor_occurred", table_name="canon_audit_events")
    op.drop_index("ix_canon_audit_events_type_occurred", table_name="canon_audit_events")
    op.drop_index(
        "ix_canon_candidates_source_status_created",
        table_name="canon_candidates",
    )
    op.drop_index(
        "ix_canon_knowledge_versions_item_status",
        table_name="canon_knowledge_versions",
    )
    op.drop_index(
        "ix_canon_knowledge_items_updated_at",
        table_name="canon_knowledge_items",
    )
    op.drop_index(
        "ix_canon_knowledge_items_domain_jurisdiction_status",
        table_name="canon_knowledge_items",
    )
    op.drop_index("ix_canon_chunks_source_model", table_name="canon_chunks")
    op.drop_index(
        "ix_canon_sources_kind_status_created",
        table_name="canon_sources",
    )
