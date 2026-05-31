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

"""Audit DTOs.

The audit log is append-only and mirrors every mutation in the
service. Compliance projections subscribe to ``flycanon.audit`` or
query ``GET /api/v1/audit`` for a paginated view.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Single audit-log row."""

    id: str
    occurred_at: datetime
    event_type: str = Field(
        description="Stable, snake-case identifier of the mutation.",
        examples=["source.ingested", "knowledge.published", "candidate.accepted"],
    )
    actor: str | None = Field(
        default=None,
        description="Stable identifier of the human or service that performed the action.",
    )
    subject_id: str = Field(description="Primary entity touched by this event.")
    subject_kind: str = Field(
        description="Type of subject: ``source`` | ``knowledge`` | ``candidate`` | ``taxonomy``.",
    )
    correlation_id: str | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    items: list[AuditEvent]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
