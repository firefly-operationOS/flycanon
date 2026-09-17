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

"""Workspace wire DTOs.

These match the ``canon_workspaces`` row shape. CRUD controllers
consume them; internal services can use them too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flycanon.interfaces.enums.workspace_status import WorkspaceStatus


class WorkspaceCreate(BaseModel):
    """Request body for ``POST /api/v1/workspaces``."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    status: WorkspaceStatus = WorkspaceStatus.active
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = Field(default=None, ge=1)
    jurisdiction: str | None = Field(default=None, max_length=64)


class WorkspaceUpdate(BaseModel):
    """Request body for ``PATCH /api/v1/workspaces/{id}``.

    Every field is optional; only fields present in the payload are
    applied.
    """

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: WorkspaceStatus | None = None
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = Field(default=None, ge=1)
    jurisdiction: str | None = Field(default=None, max_length=64)


class WorkspaceSpec(BaseModel):
    """The canonical Workspace shape returned by GET / referenced by other DTOs."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    name: str
    status: WorkspaceStatus
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = None
    jurisdiction: str | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class WorkspaceSummary(BaseModel):
    """Compact list-row shape -- omits scope + sme_roster details."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    name: str
    status: WorkspaceStatus
    retention_days: int | None = None
    jurisdiction: str | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class WorkspacePurgeResult(BaseModel):
    """Wire shape of ``POST /api/v1/workspaces/{workspace_id}:purge``.

    Every counter is the number of rows (or objects) erased by THIS
    call; a repeated purge reports zeros. ``originals_deleted`` counts
    objects the removal actually found and deleted in the object
    store, not rows that carried a key. ``closed`` is ``True`` only when
    THIS call moved the ``canon_workspaces`` row to ``closed``; it is
    ``False`` on a repeat (the row was already closed) and when no row
    existed for the id -- an implicit workspace that only ever held
    data -- in which case the data was still purged. A call that erased
    nothing and closed nothing publishes no event and writes no audit
    row. Audit rows are never counted because they are never deleted.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    workspace_id: str
    sources_removed: int = Field(ge=0)
    originals_deleted: int = Field(
        ge=0, description="Stored originals actually found and removed from the object store by this call."
    )
    chunks_removed: int = Field(ge=0)
    knowledge_items_removed: int = Field(ge=0)
    knowledge_versions_removed: int = Field(ge=0)
    citations_removed: int = Field(ge=0)
    knowledge_relations_removed: int = Field(ge=0)
    candidates_removed: int = Field(ge=0)
    conversations_removed: int = Field(ge=0)
    conversation_turns_removed: int = Field(ge=0)
    ingest_jobs_removed: int = Field(ge=0)
    ingest_job_events_removed: int = Field(ge=0)
    cost_events_removed: int = Field(ge=0)
    closed: bool = Field(
        description="Whether THIS call moved the workspace row to ``closed`` (false on a repeat)."
    )
