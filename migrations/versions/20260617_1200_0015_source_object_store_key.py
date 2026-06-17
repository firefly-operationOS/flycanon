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

"""Add canon_sources.object_store_key.

Revision ID: 0015_source_object_store_key
Revises: 0014_drop_legacy_chunk_vectors
Create Date: 2026-06-17

Adds the nullable ``object_store_key`` column to ``canon_sources``. It
holds the ObjectStore key of the persisted original document for RLM.
Nullable because existing rows -- and rows ingested with
original-document persistence disabled -- have no stored original to
point at. This migration only adds the column; populating it is wired
up in a later change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_source_object_store_key"
down_revision = "0014_drop_legacy_chunk_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("canon_sources") as batch:
        batch.add_column(sa.Column("object_store_key", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("canon_sources") as batch:
        batch.drop_column("object_store_key")
