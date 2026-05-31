# Copyright 2026 Firefly Software Solutions Inc
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
