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

"""``/api/v1/workspaces`` -- CRUD for the canonical workspace store.

Workspaces live in ``canon_workspaces``. This controller is the
user-tier CRUD surface; agent-tier callers do not have
workspace-creation rights.

Headers:

* ``X-Tenant-Id`` is the tenant scope.
* ``X-Workspace-Id`` is the caller's "current" workspace -- still
  required by :func:`tenant_context_from_request` for header
  uniformity, but the path ``{workspace_id}`` is the authoritative
  identifier for GET / PATCH / close. ``POST`` creates a row whose
  id is taken from the body.

Path conventions:

* ``POST   /api/v1/workspaces``                 -- create
* ``GET    /api/v1/workspaces``                 -- list within tenant
* ``GET    /api/v1/workspaces/{workspace_id}``  -- fetch
* ``PATCH  /api/v1/workspaces/{workspace_id}``  -- sparse update
* ``POST   /api/v1/workspaces/{workspace_id}:close`` -- close
* ``POST   /api/v1/workspaces/{workspace_id}:purge`` -- erase all data + close

The DTO field ``scope`` maps to the row column ``scope_json`` (same
shape, different name -- the column suffix marks the storage as
JSONB). The same renaming applies to ``sme_roster`` /
``sme_roster_json``. :func:`_to_spec` /
:func:`_to_summary` / :func:`_patch_to_columns` keep that translation
isolated so the wire shape and the storage shape can evolve
independently.
"""

from __future__ import annotations

import logging
from typing import Any

from pyfly.container import rest_controller
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import Body, PathVar, Valid, get_mapping, patch_mapping, post_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.events import WorkspaceEventPublisher
from flycanon.core.services.workspaces import WorkspacePurgeService
from flycanon.interfaces.dtos.workspace import (
    WorkspaceCreate,
    WorkspacePurgeResult,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
)
from flycanon.interfaces.enums.workspace_status import WorkspaceStatus
from flycanon.models.repositories import WorkspaceRepository
from flycanon.web.conventions import (
    TenantContext,
    WorkspaceNotFound,
    WorkspaceScopeMismatch,
    tenant_context_from_request,
)

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/workspaces")
class WorkspacesController:
    """REST adapter for ``canon_workspaces`` CRUD."""

    def __init__(
        self,
        repository: WorkspaceRepository,
        event_publisher: WorkspaceEventPublisher,
        purge_service: WorkspacePurgeService,
    ) -> None:
        self._repository = repository
        self._event_publisher = event_publisher
        self._purge = purge_service

    @post_mapping("", status_code=201)
    async def create(
        self,
        http_request: Request,
        body: Valid[Body[WorkspaceCreate]],
    ) -> WorkspaceSpec:
        """Create a workspace.

        The body carries the caller-chosen workspace id (slug,
        ``<= 64`` chars). Tenant id comes from the ``X-Tenant-Id``
        header -- the body intentionally does not accept it so a
        caller cannot create workspaces under another tenant.

        Returns ``201 Created`` with the persisted :class:`WorkspaceSpec`.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        row: dict[str, Any] = {
            "id": body.id,
            "tenant_id": ctx.tenant_id,
            "name": body.name,
            "status": body.status.value,
            "scope_json": body.scope,
            "sme_roster_json": body.sme_roster,
            "retention_days": body.retention_days,
            "jurisdiction": body.jurisdiction,
        }
        await self._repository.insert(row)
        saved = await self._repository.get(ctx.tenant_id, body.id)
        if saved is None:
            # Belt-and-suspenders: the insert just succeeded so the
            # read-after-write must hit. If it doesn't, something
            # below the repo is broken -- surface it loudly.
            raise RuntimeError(f"workspace {body.id!r} inserted but not readable -- check repository wiring")
        # Emit the lifecycle event after the durable write succeeded
        # (best-effort consistency; see WorkspaceEventPublisher).
        await self._event_publisher.publish_created(saved)
        return _to_spec(saved)

    @get_mapping("")
    async def list_workspaces(self, http_request: Request) -> list[WorkspaceSummary]:
        """Return every workspace owned by the caller's tenant.

        Ordered ``created_at DESC`` (the repository's contract) so the
        most-recently-opened workspaces surface first.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        rows = await self._repository.list_for_tenant(ctx.tenant_id)
        return [_to_summary(r) for r in rows]

    @get_mapping("/{workspace_id}")
    async def get(
        self,
        http_request: Request,
        workspace_id: PathVar[str],
    ) -> WorkspaceSpec:
        """Fetch a single workspace by id within the caller's tenant.

        Returns ``404 workspace_not_found`` when the
        ``(tenant_id, workspace_id)`` pair does not exist -- the
        repository's composite-key lookup guards against the
        cross-tenant leak where tenant A guesses tenant B's id.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        row = await self._repository.get(ctx.tenant_id, workspace_id)
        if row is None:
            raise WorkspaceNotFound(f"workspace {workspace_id!r} not found")
        return _to_spec(row)

    @patch_mapping("/{workspace_id}")
    async def update(
        self,
        http_request: Request,
        workspace_id: PathVar[str],
        body: Valid[Body[WorkspaceUpdate]],
    ) -> WorkspaceSpec:
        """Apply a sparse patch to a workspace row.

        Only fields present in the body (``exclude_unset=True``) are
        applied; everything else is preserved. The DTO field
        ``scope`` writes to ``scope_json`` (and ditto for
        ``sme_roster`` / ``sme_roster_json``) -- see
        :func:`_patch_to_columns`. ``updated_at`` is bumped by the
        repository.

        Returns ``404 workspace_not_found`` when the row does not
        exist under the caller's tenant.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        patch = body.model_dump(exclude_unset=True)
        columns = _patch_to_columns(patch)
        updated = await self._repository.update(ctx.tenant_id, workspace_id, columns)
        if updated is None:
            raise WorkspaceNotFound(f"workspace {workspace_id!r} not found")
        await self._event_publisher.publish_updated(updated)
        return _to_spec(updated)

    @post_mapping("/{workspace_id}:close")
    async def close(
        self,
        http_request: Request,
        workspace_id: PathVar[str],
    ) -> WorkspaceSpec:
        """Close a workspace (status='closed' + closed_at=now()).

        Idempotent at the row level: closing an already-closed
        workspace rewrites the same terminal state with a fresh
        ``closed_at`` and returns the current row. Returns
        ``404 workspace_not_found`` only when the row truly does
        not exist.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        closed = await self._repository.close(ctx.tenant_id, workspace_id)
        row = await self._repository.get(ctx.tenant_id, workspace_id)
        if row is None:
            # Either the row never existed (close() returned False
            # and the follow-up read confirms absence) or it was
            # deleted between the close and the get. Either way the
            # caller's request cannot be satisfied.
            raise WorkspaceNotFound(f"workspace {workspace_id!r} not found")
        if not closed:
            # ``close()`` returns False only when no row matched the
            # composite key, which the read above would have caught.
            # Log a warning if we ever hit this branch -- it means
            # the row appeared between the two calls.
            logger.warning(
                "close(%s, %s) returned False but the row is readable post-call",
                ctx.tenant_id,
                workspace_id,
            )
        # Close is flycanon's terminal lifecycle transition --
        # downstream consumers see this as the "delete" signal.
        await self._event_publisher.publish_deleted(
            tenant_id=ctx.tenant_id,
            workspace_id=workspace_id,
        )
        return _to_spec(row)

    @post_mapping("/{workspace_id}:purge")
    async def purge(
        self,
        http_request: Request,
        workspace_id: PathVar[str],
    ) -> WorkspacePurgeResult:
        """Erase everything the workspace holds, then close it.

        The destructive sibling of ``:close`` (which only flips the
        status). Every source is removed through the full pipeline --
        dense vectors, chunks, the stored original in the object store,
        the row, an audit entry and a ``SourceRemoved`` event each --
        then knowledge items with their versions, citations and
        relations, candidates, conversations and turns, ingest jobs and
        events, and cost events are deleted in one transaction, the
        workspace row is moved to ``closed`` and a ``WorkspaceDeleted``
        event is emitted. Audit rows are kept (the log is append-only)
        and a ``workspace.purged`` row records the counts.

        ``X-Workspace-Id`` MUST equal the path id
        (``400 workspace_scope_mismatch`` otherwise): under the
        production RLS role the header is what scopes every row the
        request can see, so a purge aimed at a different path id would
        silently do nothing -- or, on a BYPASSRLS dev role, hit the
        wrong workspace. Requiring agreement makes the verb mean the
        same thing on both roles.

        Idempotent: a workspace with nothing left (or one that never had
        a ``canon_workspaces`` row) returns ``200`` with zero counts and
        ``closed=false``, so an off-boarding job can retry.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        if workspace_id != ctx.workspace_id:
            raise WorkspaceScopeMismatch(
                f"X-Workspace-Id {ctx.workspace_id!r} must equal the path workspace {workspace_id!r}."
            )
        report = await self._purge.purge(
            tenant_id=ctx.tenant_id,
            workspace_id=workspace_id,
            actor=ctx.actor,
            correlation_id=get_correlation_id(),
        )
        counts = report.counts
        return WorkspacePurgeResult(
            tenant_id=report.tenant_id,
            workspace_id=report.workspace_id,
            sources_removed=report.sources_removed,
            originals_deleted=report.originals_deleted,
            chunks_removed=counts.get("chunks", 0),
            knowledge_items_removed=counts.get("knowledge_items", 0),
            knowledge_versions_removed=counts.get("knowledge_versions", 0),
            citations_removed=counts.get("citations", 0),
            knowledge_relations_removed=counts.get("knowledge_relations", 0),
            candidates_removed=counts.get("candidates", 0),
            conversations_removed=counts.get("conversations", 0),
            conversation_turns_removed=counts.get("conversation_turns", 0),
            ingest_jobs_removed=counts.get("ingest_jobs", 0),
            ingest_job_events_removed=counts.get("ingest_job_events", 0),
            cost_events_removed=counts.get("cost_events", 0),
            closed=report.closed,
        )


# ----------------------------------------------------------------------
# DTO <-> row helpers
# ----------------------------------------------------------------------


def _to_spec(row: dict[str, Any]) -> WorkspaceSpec:
    """Translate a repository row dict into the wire :class:`WorkspaceSpec`."""
    return WorkspaceSpec(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        status=WorkspaceStatus(row["status"]),
        scope=row.get("scope_json"),
        sme_roster=row.get("sme_roster_json"),
        retention_days=row.get("retention_days"),
        jurisdiction=row.get("jurisdiction"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row.get("closed_at"),
    )


def _to_summary(row: dict[str, Any]) -> WorkspaceSummary:
    """Translate a repository row dict into the compact :class:`WorkspaceSummary`."""
    return WorkspaceSummary(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        status=WorkspaceStatus(row["status"]),
        retention_days=row.get("retention_days"),
        jurisdiction=row.get("jurisdiction"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row.get("closed_at"),
    )


def _patch_to_columns(patch: dict[str, Any]) -> dict[str, Any]:
    """Rename DTO fields to entity columns for a sparse patch.

    The DTO carries ``scope`` / ``sme_roster``; the row carries
    ``scope_json`` / ``sme_roster_json``. Every other field passes
    through unchanged. Status is unwrapped from the enum to the
    string value the column expects.
    """
    columns: dict[str, Any] = {}
    for key, value in patch.items():
        if key == "scope":
            columns["scope_json"] = value
        elif key == "sme_roster":
            columns["sme_roster_json"] = value
        elif key == "status" and isinstance(value, WorkspaceStatus):
            columns["status"] = value.value
        else:
            columns[key] = value
    return columns
