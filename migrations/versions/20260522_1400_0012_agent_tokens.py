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

"""Agent tokens table.

Revision ID: 0012_agent_tokens
Revises: 0011_tighten_scope
Create Date: 2026-05-22

Stores tenant-scoped agent tokens that gate the ``/api/v1/agent/*``
surface. The raw secret is never persisted; we keep a visible prefix
(lookup key) plus a salted hash of the full token. The
``workspace_allowlist_json`` column is NULL when the token is valid
for every workspace in the tenant; otherwise it carries a JSON array
of workspace_ids. ``scopes_json`` is a JSON array of route patterns
the token is allowed to hit (e.g.
``["agent.answer", "agent.search"]``).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_agent_tokens"
down_revision = "0011_tighten_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canon_agent_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("workspace_allowlist_json", sa.JSON(), nullable=True),
        sa.Column("scopes_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_canon_agent_tokens_prefix",
        "canon_agent_tokens",
        ["prefix"],
        unique=True,
    )
    op.create_index(
        "ix_canon_agent_tokens_tenant_active",
        "canon_agent_tokens",
        ["tenant_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canon_agent_tokens_tenant_active",
        table_name="canon_agent_tokens",
    )
    op.drop_index(
        "uq_canon_agent_tokens_prefix",
        table_name="canon_agent_tokens",
    )
    op.drop_table("canon_agent_tokens")
