# Copyright 2026 Firefly Software Solutions Inc
"""Embeddings scope -- canon_chunks re-embed + scope-source indexes.

Revision ID: 0009_embeddings_scope
Revises: 0008_workspaces
Create Date: 2026-05-22

Plan 2 added ``(tenant_id, workspace_id)`` to ``canon_chunks`` but
kept the original ``(source_id, embedding_model)`` re-embed-drift
index. Per the unification spec section 4.3, the right composite
for re-embed scope is ``(tenant_id, workspace_id, embedding_model)``
-- re-embed jobs are workspace-scoped, not source-scoped.

Adds:
- ``ix_canon_chunks_tenant_workspace_model`` -- the new re-embed
  drift detector.
- ``ix_canon_chunks_scope_source`` -- composite for chunk fetches
  by source within a workspace.

Drops:
- ``ix_canon_chunks_source_model`` (replaced).
"""

from __future__ import annotations

from alembic import op

revision = "0009_embeddings_scope"
down_revision = "0008_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_canon_chunks_source_model", table_name="canon_chunks")
    op.create_index(
        "ix_canon_chunks_tenant_workspace_model",
        "canon_chunks",
        ["tenant_id", "workspace_id", "embedding_model"],
    )
    op.create_index(
        "ix_canon_chunks_scope_source",
        "canon_chunks",
        ["tenant_id", "workspace_id", "source_id", "index_in_source"],
    )


def downgrade() -> None:
    op.drop_index("ix_canon_chunks_scope_source", table_name="canon_chunks")
    op.drop_index("ix_canon_chunks_tenant_workspace_model", table_name="canon_chunks")
    op.create_index(
        "ix_canon_chunks_source_model",
        "canon_chunks",
        ["source_id", "embedding_model"],
    )
