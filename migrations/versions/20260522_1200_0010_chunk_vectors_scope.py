# Copyright 2026 Firefly Software Solutions Inc
"""Add tenant_id + workspace_id to canon_chunk_vectors.

Revision ID: 0010_chunk_vectors_scope
Revises: 0009_embeddings_scope
Create Date: 2026-05-22

``canon_chunk_vectors`` is RUNTIME-created by
``PgVectorVectorStore.initialise()`` (it lives outside Alembic
because the pgvector extension may not be installed everywhere).
This migration ALTERs the table IF IT EXISTS, adding scope
columns + a composite index. On SQLite (where the table never
exists), it's a no-op.

The new ``PgVectorVectorStore`` DDL (Task 2 of Plan 3) creates
the table with the scope columns from the start, so this
migration is only needed for clusters that already had the table
before Plan 3.
"""

from __future__ import annotations

from alembic import op

revision = "0010_chunk_vectors_scope"
down_revision = "0009_embeddings_scope"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'canon_chunk_vectors'
          ) THEN
            ALTER TABLE canon_chunk_vectors
              ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
            ALTER TABLE canon_chunk_vectors
              ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT 'default';
            CREATE INDEX IF NOT EXISTS canon_chunk_vectors_scope
              ON canon_chunk_vectors (tenant_id, workspace_id);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'canon_chunk_vectors'
          ) THEN
            DROP INDEX IF EXISTS canon_chunk_vectors_scope;
            ALTER TABLE canon_chunk_vectors DROP COLUMN IF EXISTS workspace_id;
            ALTER TABLE canon_chunk_vectors DROP COLUMN IF EXISTS tenant_id;
          END IF;
        END $$;
        """
    )
