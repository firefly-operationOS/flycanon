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

"""``canon_workspaces`` -- canonical workspace identity + metadata.

A workspace scopes every other canon_* row through the ``tenant_id`` +
``workspace_id`` composite carried across the schema. The row stores
the human-facing name, the lifecycle
status, optional scope / SME roster metadata, and retention / jurisdiction
defaults that downstream services consult when materialising knowledge.

The mapping mirrors migration ``0008_workspaces`` one-for-one: column
types, server defaults, and the ``(tenant_id, id)`` uniqueness contract
are kept in lock-step so ``alembic check`` stays green.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class Workspace(Base):
    __tablename__ = "canon_workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text("'active'"),
        default="active",
    )

    # Optional metadata -- shape governed by the WorkspaceSpec DTO.
    scope_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    sme_roster_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Bookkeeping.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_canon_workspaces_tenant_id"),)
