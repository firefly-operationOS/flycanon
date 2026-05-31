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

``PgVectorVectorStore`` creates the table with the scope columns
from the start, so this migration only applies to clusters whose
``canon_chunk_vectors`` table predates the scope columns.
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
