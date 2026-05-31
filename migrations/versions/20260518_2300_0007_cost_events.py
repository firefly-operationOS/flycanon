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

"""Per-call LLM cost tracking.

Revision ID: 0007_cost_events
Revises: 0006_conversations
Create Date: 2026-05-18 23:00:00 UTC

Every FireflyAgent call emits an ``agent.completed`` event that
carries ``input_tokens``, ``output_tokens``, ``cost_usd``, and the
``model`` id. We collect those into ``canon_cost_events`` so the
``GET /api/v1/billing`` endpoint can aggregate spend by day /
model / agent name, and operators see "this tenant burned $4.20
yesterday" before the invoice arrives.

Each row carries enough breadcrumbs to:

* group by tenant (``actor`` is the best v1 proxy until
  multi-tenant lands properly),
* group by agent name (``flycanon-consolidator`` vs
  ``flycanon-answerer`` vs ``flycanon-suggester``, ...),
* split by model so an ``anthropic:claude-sonnet-4-6`` vs
  ``openai:gpt-4o`` cost comparison is one SUM-GROUP-BY query.

The middleware that emits the event is in ``fireflyframework-pyfly``
-- this commit lands the persistence + aggregation surface.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_cost_events"
down_revision = "0006_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canon_cost_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("subject_kind", sa.String(length=64), nullable=True),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_canon_cost_events_occurred",
        "canon_cost_events",
        ["occurred_at"],
    )
    op.create_index(
        "ix_canon_cost_events_actor_occurred",
        "canon_cost_events",
        ["actor", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_canon_cost_events_actor_occurred", table_name="canon_cost_events")
    op.drop_index("ix_canon_cost_events_occurred", table_name="canon_cost_events")
    op.drop_table("canon_cost_events")
