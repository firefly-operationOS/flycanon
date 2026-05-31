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

"""Initial schema for the flycanon Operational Knowledge Repository.

Revision ID: 0001_init
Revises:
Create Date: 2026-05-18 17:00:00 UTC

Tables landed in this migration:

* ``canon_sources``             -- raw inbound artefacts (no bytes stored)
* ``canon_chunks``              -- retrieval-grade fragments with optional embeddings
* ``canon_knowledge_items``     -- canonical item pointers
* ``canon_knowledge_versions``  -- per-revision content rows
* ``canon_citations``           -- (knowledge_version, chunk) edges
* ``canon_candidates``          -- pre-canonical proposals from consolidation
* ``canon_audit_events``        -- append-only audit log
* ``canon_taxonomy_nodes``      -- domain / jurisdiction tree

The default-taxonomy seed lands in a follow-up data migration; this
revision is schema-only so destructive ``downgrade`` stays clean.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- canon_sources ------------------------------------------------
    op.create_table(
        "canon_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=True),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canon_sources_kind", "canon_sources", ["kind"])
    op.create_index("ix_canon_sources_status", "canon_sources", ["status"])
    op.create_index("ix_canon_sources_content_sha256", "canon_sources", ["content_sha256"])
    op.create_index(
        "uq_canon_sources_content_sha256",
        "canon_sources",
        ["content_sha256"],
        unique=True,
        postgresql_where=sa.text("content_sha256 IS NOT NULL"),
        sqlite_where=sa.text("content_sha256 IS NOT NULL"),
    )

    # --- canon_chunks -------------------------------------------------
    op.create_table(
        "canon_chunks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(length=36),
            sa.ForeignKey("canon_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index_in_source", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("char_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canon_chunks_source_id", "canon_chunks", ["source_id"])
    op.create_index("ix_canon_chunks_source_idx", "canon_chunks", ["source_id", "index_in_source"])

    # --- canon_knowledge_items ---------------------------------------
    op.create_table(
        "canon_knowledge_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=False, server_default="GLOBAL"),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("superseded_by_item_id", sa.String(length=36), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canon_knowledge_items_status", "canon_knowledge_items", ["status"])
    op.create_index("ix_canon_knowledge_items_domain", "canon_knowledge_items", ["domain"])
    op.create_index("ix_canon_knowledge_items_jurisdiction", "canon_knowledge_items", ["jurisdiction"])
    op.create_index(
        "ix_canon_knowledge_items_domain_status",
        "canon_knowledge_items",
        ["domain", "status"],
    )

    # --- canon_knowledge_versions ------------------------------------
    op.create_table(
        "canon_knowledge_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "knowledge_item_id",
            sa.String(length=36),
            sa.ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=False, server_default="GLOBAL"),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("supersedes_version", sa.Integer(), nullable=True),
        sa.Column("superseded_by_version", sa.Integer(), nullable=True),
        sa.Column("originating_candidate_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint(
            "knowledge_item_id",
            "version",
            name="uq_canon_knowledge_versions_item_version",
        ),
    )
    op.create_index(
        "ix_canon_knowledge_versions_knowledge_item_id",
        "canon_knowledge_versions",
        ["knowledge_item_id"],
    )
    op.create_index("ix_canon_knowledge_versions_status", "canon_knowledge_versions", ["status"])
    op.create_index("ix_canon_knowledge_versions_domain", "canon_knowledge_versions", ["domain"])

    # --- canon_citations ---------------------------------------------
    op.create_table(
        "canon_citations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "knowledge_version_id",
            sa.String(length=36),
            sa.ForeignKey("canon_knowledge_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            sa.String(length=36),
            sa.ForeignKey("canon_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_id",
            sa.String(length=36),
            sa.ForeignKey("canon_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("relevance", sa.Float(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canon_citations_knowledge_version_id", "canon_citations", ["knowledge_version_id"])
    op.create_index("ix_canon_citations_chunk_id", "canon_citations", ["chunk_id"])
    op.create_index("ix_canon_citations_source_id", "canon_citations", ["source_id"])
    op.create_index(
        "ix_canon_citations_version_chunk",
        "canon_citations",
        ["knowledge_version_id", "chunk_id"],
    )

    # --- canon_candidates --------------------------------------------
    op.create_table(
        "canon_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "source_id",
            sa.String(length=36),
            sa.ForeignKey("canon_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=False, server_default="GLOBAL"),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("citations_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("materialised_knowledge_item_id", sa.String(length=36), nullable=True),
        sa.Column("materialised_version", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canon_candidates_status", "canon_candidates", ["status"])
    op.create_index("ix_canon_candidates_source_id", "canon_candidates", ["source_id"])
    op.create_index("ix_canon_candidates_domain", "canon_candidates", ["domain"])
    op.create_index("ix_canon_candidates_source_status", "canon_candidates", ["source_id", "status"])

    # --- canon_audit_events ------------------------------------------
    op.create_table(
        "canon_audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canon_audit_events_event_type", "canon_audit_events", ["event_type"])
    op.create_index("ix_canon_audit_events_subject_id", "canon_audit_events", ["subject_id"])
    op.create_index("ix_canon_audit_events_subject_kind", "canon_audit_events", ["subject_kind"])
    op.create_index("ix_canon_audit_events_actor", "canon_audit_events", ["actor"])
    op.create_index("ix_canon_audit_events_occurred_at", "canon_audit_events", ["occurred_at"])
    op.create_index("ix_canon_audit_subject", "canon_audit_events", ["subject_kind", "subject_id"])

    # --- canon_taxonomy_nodes ----------------------------------------
    op.create_table(
        "canon_taxonomy_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "parent_id",
            sa.String(length=36),
            sa.ForeignKey("canon_taxonomy_nodes.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("parent_id", "slug", name="uq_canon_taxonomy_parent_slug"),
    )
    op.create_index("ix_canon_taxonomy_nodes_parent_id", "canon_taxonomy_nodes", ["parent_id"])
    op.create_index("ix_canon_taxonomy_nodes_domain", "canon_taxonomy_nodes", ["domain"])
    op.create_index(
        "ix_canon_taxonomy_domain_depth",
        "canon_taxonomy_nodes",
        ["domain", "depth"],
    )


def downgrade() -> None:
    op.drop_table("canon_taxonomy_nodes")
    op.drop_table("canon_audit_events")
    op.drop_table("canon_candidates")
    op.drop_table("canon_citations")
    op.drop_table("canon_knowledge_versions")
    op.drop_table("canon_knowledge_items")
    op.drop_table("canon_chunks")
    op.drop_table("canon_sources")
