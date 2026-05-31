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

"""``canon_knowledge_items`` -- canonical, versioned knowledge units.

The item row tracks the current state -- which version is canonical,
which status it sits in, its domain / jurisdiction header. Every
change to the actual content lands as a new
:class:`KnowledgeVersionRow`; the item row never gets in-place
mutations to ``title`` / ``body`` / ``summary`` (those live on the
version), only to its lifecycle status and pointers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class KnowledgeItemRow(Base):
    __tablename__ = "canon_knowledge_items"

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

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="GLOBAL", index=True)

    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Lifecycle pointers.
    superseded_by_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_canon_knowledge_items_domain_status", "domain", "status"),
        Index("ix_canon_knowledge_items_tenant_workspace", "tenant_id", "workspace_id"),
    )
