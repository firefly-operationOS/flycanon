# Copyright 2026 Firefly Software Solutions Inc
"""Tighten scope constraints + drop the ``'default'`` server defaults.

Revision ID: 0011_tighten_scope
Revises: 0010_chunk_vectors_scope
Create Date: 2026-05-22

Every service threads real ``TenantContext`` values, so the
column-level ``'default'`` server defaults on ``tenant_id`` +
``workspace_id`` are not needed.

This migration:

1. Drops the ``server_default=text("'default'")`` from ``tenant_id`` +
   ``workspace_id`` on each of the 14 scoped ``canon_*`` tables.
2. Widens the partial unique index on
   ``canon_sources.content_sha256`` to include ``(tenant_id,
   workspace_id, content_sha256)`` so the SHA-256 idempotency anchor
   is workspace-scoped -- the same bytes uploaded into two workspaces
   must each get their own ``canon_sources`` row.

Other ``UniqueConstraint``s on ``canon_*`` tables stay narrow because
they already carry their scope transitively via a FK to a parent
table that is itself scoped:

* ``canon_knowledge_versions(knowledge_item_id, version)`` -- the
  item is scoped, so a duplicate version under a different scope is
  structurally impossible.
* ``canon_knowledge_relations(from_item_id, to_item_id, kind)`` -- same.
* ``canon_conversation_turns(conversation_id, turn_index)`` -- same.
* ``canon_taxonomy_nodes(parent_id, slug)`` -- a child node inherits
  its parent's scope; the root node's scope is enforced by the
  ``(tenant_id, workspace_id)`` index instead.

Uses ``batch_alter_table`` for SQLite compatibility. Downgrade is
fully reversible.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_tighten_scope"
down_revision = "0010_chunk_vectors_scope"
branch_labels = None
depends_on = None


# Order matches migration 0008. Every table on this list has both
# ``tenant_id`` and ``workspace_id`` columns added by 0008 with the
# ``'default'`` server default that we drop here.
_TENANT_WORKSPACE_TABLES = [
    "canon_sources",
    "canon_chunks",
    "canon_knowledge_items",
    "canon_knowledge_versions",
    "canon_citations",
    "canon_candidates",
    "canon_knowledge_relations",
    "canon_audit_events",
    "canon_conversations",
    "canon_conversation_turns",
    "canon_ingest_jobs",
    "canon_ingest_job_events",
    "canon_cost_events",
    "canon_taxonomy_nodes",
]


def upgrade() -> None:
    # ---- 1) drop the ``'default'`` server defaults --------------------
    for table in _TENANT_WORKSPACE_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=64),
                server_default=None,
                existing_nullable=False,
            )
            batch.alter_column(
                "workspace_id",
                existing_type=sa.String(length=64),
                server_default=None,
                existing_nullable=False,
            )

    # ---- 2) widen the canon_sources content-sha256 unique index -------
    #
    # ``uq_canon_sources_content_sha256`` is a *partial* unique INDEX
    # (not a UniqueConstraint), with ``WHERE content_sha256 IS NOT
    # NULL`` so empty-hash rows are allowed in flight. We drop the
    # narrow index and recreate the composite under the same partial
    # predicate.
    op.drop_index("uq_canon_sources_content_sha256", table_name="canon_sources")
    op.create_index(
        "uq_canon_sources_tenant_workspace_content_sha256",
        "canon_sources",
        ["tenant_id", "workspace_id", "content_sha256"],
        unique=True,
        postgresql_where=sa.text("content_sha256 IS NOT NULL"),
        sqlite_where=sa.text("content_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    # ---- 1) restore the narrow unique index ---------------------------
    op.drop_index(
        "uq_canon_sources_tenant_workspace_content_sha256",
        table_name="canon_sources",
    )
    op.create_index(
        "uq_canon_sources_content_sha256",
        "canon_sources",
        ["content_sha256"],
        unique=True,
        postgresql_where=sa.text("content_sha256 IS NOT NULL"),
        sqlite_where=sa.text("content_sha256 IS NOT NULL"),
    )

    # ---- 2) restore the ``'default'`` server defaults -----------------
    for table in _TENANT_WORKSPACE_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=64),
                server_default=sa.text("'default'"),
                existing_nullable=False,
            )
            batch.alter_column(
                "workspace_id",
                existing_type=sa.String(length=64),
                server_default=sa.text("'default'"),
                existing_nullable=False,
            )
