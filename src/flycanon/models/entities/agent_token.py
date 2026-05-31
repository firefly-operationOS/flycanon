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

"""``canon_agent_tokens`` -- tenant-scoped agent tokens.

The raw token secret is never persisted; we keep the visible
``prefix`` (lookup key) plus a salted ``secret_hash``. A NULL
``workspace_allowlist_json`` means the token is valid for every
workspace in the tenant; a JSON array restricts it to those
workspace_ids. ``scopes_json`` is a JSON array of route patterns the
token may hit (e.g. ``["agent.answer", "agent.search"]``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class AgentToken(Base):
    __tablename__ = "canon_agent_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_allowlist_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, server_default=text("'[]'"))
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_canon_agent_tokens_prefix", "prefix", unique=True),
        Index("ix_canon_agent_tokens_tenant_active", "tenant_id", "revoked_at"),
    )
