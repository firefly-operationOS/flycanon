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

"""``canon_knowledge_relations`` -- typed edges between knowledge items.

Each row carries a directed link ``from_item_id -> to_item_id`` with
a ``kind`` discriminator (``related``, ``depends_on``,
``conflicts_with``, ``replaces``) and optional metadata (``note``,
``actor``, ``since_version``). The graph is read by the provenance
endpoint + the knowledge-graph visualisation endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class KnowledgeRelationRow(Base):
    __tablename__ = "canon_knowledge_relations"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    from_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_item_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    since_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "from_item_id",
            "to_item_id",
            "kind",
            name="uq_canon_knowledge_relations_from_to_kind",
        ),
        Index(
            "ix_canon_knowledge_relations_tenant_workspace",
            "tenant_id",
            "workspace_id",
        ),
    )
