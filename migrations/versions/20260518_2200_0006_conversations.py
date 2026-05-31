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

"""Conversational RAG sessions.

Revision ID: 0006_conversations
Revises: 0005_ingest_jobs
Create Date: 2026-05-18 22:00:00 UTC

The ``/query`` endpoint is conversation-less today -- every call
re-grounds from scratch. Multi-turn chat clients have to stitch
"and what about Y?" onto the original question themselves; the
RAG context drifts as a result. This migration lands the
persistence layer for conversational RAG:

* ``canon_conversations`` -- the chat session itself.
* ``canon_conversation_turns`` -- append-only per-turn record
  (question, retrieved citations, answer, model, elapsed_ms).
* Rolling ``summary`` on the parent row -- a compact synopsis the
  next-turn prompt builder prepends so long sessions still fit in
  the model's context window.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_conversations"
down_revision = "0005_ingest_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canon_conversations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "canon_conversation_turns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("canon_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations_json", sa.JSON, nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("no_answer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "turn_index",
            name="uq_canon_conversation_turns_conv_turn",
        ),
    )
    op.create_index(
        "ix_canon_conversation_turns_conv_created",
        "canon_conversation_turns",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canon_conversation_turns_conv_created",
        table_name="canon_conversation_turns",
    )
    op.drop_table("canon_conversation_turns")
    op.drop_table("canon_conversations")
