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

"""``canon_candidates`` -- pre-canonical knowledge proposals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class CandidateRow(Base):
    __tablename__ = "canon_candidates"

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

    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("canon_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Proposed content.
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="GLOBAL")
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Resolved citations (chunk-anchored) serialised as JSON so we can
    # store the consolidator's full proposal before the human decides;
    # accepted candidates fan their citations out into ``canon_citations``.
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Materialisation pointers (set on accept).
    materialised_knowledge_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    materialised_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_canon_candidates_source_status", "source_id", "status"),
        Index("ix_canon_candidates_tenant_workspace", "tenant_id", "workspace_id"),
    )
