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

"""Embeddings scope -- canon_chunks re-embed + scope-source indexes.

Revision ID: 0009_embeddings_scope
Revises: 0008_workspaces
Create Date: 2026-05-22

Re-embed jobs are workspace-scoped, so the composite that backs
re-embed-drift detection on ``canon_chunks`` is
``(tenant_id, workspace_id, embedding_model)``.

Adds:
- ``ix_canon_chunks_tenant_workspace_model`` -- the re-embed drift
  detector.
- ``ix_canon_chunks_scope_source`` -- composite for chunk fetches
  by source within a workspace.

Drops:
- ``ix_canon_chunks_source_model``.
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
