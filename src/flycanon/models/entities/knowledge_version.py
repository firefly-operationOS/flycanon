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

"""``canon_knowledge_versions`` -- the per-revision content row.

Every published / draft revision of a knowledge item gets a row here.
The combination ``(knowledge_item_id, version)`` is unique; the item's
``current_version`` points at the active row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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


class KnowledgeVersionRow(Base):
    __tablename__ = "canon_knowledge_versions"

    id: Mapped[str] = mapped_column(
        String(36),
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
    knowledge_item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("canon_knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    # Content.
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Header.
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="GLOBAL")
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Version chain.
    supersedes_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    superseded_by_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Origin trace.
    originating_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_item_id",
            "version",
            name="uq_canon_knowledge_versions_item_version",
        ),
        Index(
            "ix_canon_knowledge_versions_tenant_workspace",
            "tenant_id",
            "workspace_id",
        ),
    )
