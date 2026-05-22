# Copyright 2026 Firefly Software Solutions Inc
"""Workspaces table + tenant/workspace scope on every canon_* row.

Revision ID: 0008_workspaces
Revises: 0007_cost_events
Create Date: 2026-05-22

This migration:

1. Creates ``canon_workspaces`` -- the canonical store for workspace
   identity + metadata (per unification spec section 4.1).
2. Adds ``tenant_id`` + ``workspace_id`` as NOT NULL columns with
   server default ``'default'`` to every existing canon table.
3. Backfills existing rows to ``'default'``/``'default'``.
4. Adds a composite ``(tenant_id, workspace_id)`` index on each
   table to support workspace-scoped queries.

The server default lets existing services keep writing without
changes. Plan 4 (conventions adoption) will replace the default
with real ``TenantContext`` values, drop the defaults from
entities, and widen unique constraints.

Uses ``batch_alter_table`` for SQLite compatibility.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_workspaces"
down_revision = "0007_cost_events"
branch_labels = None
depends_on = None


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
    # ---- canon_workspaces (new table) -----------------------------
    op.create_table(
        "canon_workspaces",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("scope_json", sa.JSON(), nullable=True),
        sa.Column("sme_roster_json", sa.JSON(), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("jurisdiction", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_canon_workspaces_tenant_id"),
    )

    # ---- add scope columns to all existing tables -----------------
    for table in _TENANT_WORKSPACE_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "tenant_id",
                    sa.String(length=64),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )
            batch.add_column(
                sa.Column(
                    "workspace_id",
                    sa.String(length=64),
                    nullable=False,
                    server_default=sa.text("'default'"),
                )
            )
        op.create_index(
            f"ix_{table}_tenant_workspace",
            table,
            ["tenant_id", "workspace_id"],
        )


def downgrade() -> None:
    for table in _TENANT_WORKSPACE_TABLES:
        op.drop_index(f"ix_{table}_tenant_workspace", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("workspace_id")
            batch.drop_column("tenant_id")
    op.drop_table("canon_workspaces")
