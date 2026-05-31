# Copyright 2026 Firefly Software Solutions Inc
"""Postgres-native BM25 projection on ``canon_chunks``.

Revision ID: 0003_bm25_tsv
Revises: 0002_indexes
Create Date: 2026-05-18 18:00:00 UTC

Builds the BM25 projection as a Postgres ``tsvector`` + ``GIN``
index. The dense projection lives in ``canon_chunk_vectors`` via
pgvector; this migration makes the BM25 projection co-resident so
hybrid retrieval is a single-host operation.

What changes:

* Adds a ``tsv`` GENERATED ALWAYS column on ``canon_chunks`` that
  serialises ``content`` to a ``tsvector`` using the ``simple``
  text-search configuration. ``simple`` lower-cases tokens and
  splits on whitespace / punctuation without language-specific
  stemming -- the right default for a multilingual corpus
  (Spanish, English, Catalan, etc.). Switch to ``english`` /
  ``spanish`` per-tenant when the deployment is mono-lingual.
* Creates a ``GIN`` index on ``tsv`` for sub-millisecond BM25
  lookups: ``WHERE tsv @@ plainto_tsquery('simple', :q)``.

The column is GENERATED so application writes don't have to
maintain it; every INSERT / UPDATE on ``canon_chunks.content``
refreshes the projection atomically in the same transaction.

Postgres-only: the ``tsvector`` / GIN DDL is skipped on SQLite (used
by the test suite), which lacks those features.
"""

from __future__ import annotations

from alembic import op

revision = "0003_bm25_tsv"
down_revision = "0002_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Skip on SQLite (used by the test suite); the dialect lacks
    # ``tsvector`` / GIN.
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        ALTER TABLE canon_chunks
        ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS canon_chunks_tsv_gin
        ON canon_chunks USING GIN (tsv)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS canon_chunks_tsv_gin")
    op.execute("ALTER TABLE canon_chunks DROP COLUMN IF EXISTS tsv")
