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

"""Workspace lifecycle event DTOs -- payloads emitted on
``canon.workspaces.v1``.

Every successful mutation in :class:`flycanon.web.controllers.workspaces_controller.WorkspacesController`
publishes one of these events. Consumers (e.g., flyradar's workspace
cache client) use them to keep a local projection of the canonical
workspace registry without polling.

The payload mirrors :class:`flycanon.interfaces.dtos.workspace.WorkspaceSpec`
so a consumer can rebuild its cache row directly from the event body --
field names match the wire DTO, not the storage columns
(``scope`` not ``scope_json``).

Lifecycle mapping (flycanon-specific):

* ``WorkspaceCreated``  -- emitted from ``POST /api/v1/workspaces``.
* ``WorkspaceUpdated``  -- emitted from ``PATCH /api/v1/workspaces/{id}``.
  flycanon uses PATCH for sparse updates; there is no PUT.
* ``WorkspaceDeleted``  -- emitted from ``POST /api/v1/workspaces/{id}:close``.
  flycanon has no hard-delete route -- closing a workspace is the
  terminal lifecycle transition; the row is preserved for audit. The
  event name ``WorkspaceDeleted`` matches the canonical lifecycle
  vocabulary so downstream consumers don't have to special-case
  flycanon's soft-delete semantics.

All three event types live on the same topic ``canon.workspaces.v1``;
consumers dispatch on ``event_type``. The literal values use the
``workspace.*`` snake-case convention shared with the audit-log
event type names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceEventBase(BaseModel):
    """Shared fields for every workspace lifecycle event.

    ``tenant_id`` + ``workspace_id`` carry the canonical (tenant,
    workspace) scope ubiquitous-language pair. ``occurred_at`` is the
    event publisher's clock at emit time -- timezone-aware UTC.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    occurred_at: datetime


class WorkspaceCreated(WorkspaceEventBase):
    """A new workspace row landed in ``canon_workspaces``.

    Payload mirrors the wire :class:`WorkspaceSpec`: the consumer can
    build the cache row directly from these fields. ``scope`` and
    ``sme_roster`` carry the JSON shapes as-stored.
    """

    event_type: Literal["workspace.created"] = "workspace.created"
    name: str = Field(min_length=1, max_length=255)
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = None
    jurisdiction: str | None = None


class WorkspaceUpdated(WorkspaceEventBase):
    """An existing workspace row was sparsely patched.

    The event carries the **post-update** row state (not just the
    patched fields) so consumers can replace their cached row
    wholesale rather than diff-apply -- same payload shape as
    :class:`WorkspaceCreated` for symmetry.
    """

    event_type: Literal["workspace.updated"] = "workspace.updated"
    name: str = Field(min_length=1, max_length=255)
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = None
    jurisdiction: str | None = None


class WorkspaceDeleted(WorkspaceEventBase):
    """A workspace was closed (flycanon's terminal lifecycle state).

    No payload fields beyond the base -- the (tenant_id, workspace_id)
    pair is enough for the consumer to drop or tombstone its cached
    row. The original row remains in ``canon_workspaces`` for audit.
    """

    event_type: Literal["workspace.deleted"] = "workspace.deleted"
