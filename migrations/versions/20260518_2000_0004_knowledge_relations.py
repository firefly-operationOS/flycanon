# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge-item relations + per-(from, to, kind) uniqueness.

Revision ID: 0004_knowledge_relations
Revises: 0003_bm25_tsv
Create Date: 2026-05-18 20:00:00 UTC

Adds ``canon_knowledge_relations`` -- the typed graph of semantic
links between knowledge items. Four ``kind`` values land in v1:

* ``related``        -- soft "see also" link.
* ``depends_on``     -- ``from`` requires ``to`` to remain valid.
* ``conflicts_with`` -- ``from`` and ``to`` make contradictory
                        claims (typically surfaced by the
                        conflict-detection background job).
* ``replaces``       -- ``from`` formally replaces ``to`` (a
                        retrospective on a superseded item or a
                        cross-item replacement).

The relation is **directed** -- ``A depends_on B`` is not the same
as ``B depends_on A``. A unique constraint on
``(from_item_id, to_item_id, kind)`` keeps the graph from accreting
duplicates when the same link is asserted twice.

Both endpoints are FK-protected: deleting a knowledge item
cascades to its relation rows so the graph never points at
ghosts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_knowledge_relations"
down_revision = "0003_bm25_tsv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canon_knowledge_relations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "from_item_id",
            sa.String(length=64),
            sa.ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_item_id",
            sa.String(length=64),
            sa.ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("since_version", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "from_item_id",
            "to_item_id",
            "kind",
            name="uq_canon_knowledge_relations_from_to_kind",
        ),
    )
    op.create_index(
        "ix_canon_knowledge_relations_from",
        "canon_knowledge_relations",
        ["from_item_id"],
    )
    op.create_index(
        "ix_canon_knowledge_relations_to",
        "canon_knowledge_relations",
        ["to_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canon_knowledge_relations_to",
        table_name="canon_knowledge_relations",
    )
    op.drop_index(
        "ix_canon_knowledge_relations_from",
        table_name="canon_knowledge_relations",
    )
    op.drop_table("canon_knowledge_relations")
