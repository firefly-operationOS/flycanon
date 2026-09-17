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

"""``WorkspacePurgeService`` -- erase everything a workspace holds.

Backs ``POST /api/v1/workspaces/{workspace_id}:purge``, the verb a
multi-tenant caller needs for tenant off-boarding and for retention
deletes that must reach the bytes, not just the index. ``:close`` only
flips ``status``; the data stays. Purge is the destructive sibling.

Order of operations, and why it is this order:

1. **Sources first, one at a time, through the normal removal path.**
   :meth:`IntakeService.remove` is the only code that knows how to
   drop a source's dense vectors from whichever vector backend is
   configured, delete the chunk rows, delete the stored original from
   the object store, and write the audit row + ``SourceRemoved`` event
   the downstream projections listen for. Re-implementing that as a
   bulk statement would silently skip the vector store and the object
   store. Sources are paged from the repository until the scope is
   empty, so a workspace with thousands of documents is handled
   without loading them all at once.
2. **Then the scope-wide sweep** (:class:`ScopePurgeRepository`):
   knowledge items with versions, citations and relations; candidates;
   conversations and turns; ingest jobs and events; cost events; and
   any chunk left behind. One transaction, dependency order.
3. **Then the workspace row is closed** (not deleted). The row is the
   only durable evidence the slug ever existed; keeping it as
   ``closed`` means a later ``POST /workspaces`` with the same id fails
   loudly instead of silently resurrecting a purged scope, and the
   ``WorkspaceDeleted`` lifecycle event is emitted for consumers.
4. **Finally an audit row** ``workspace.purged`` with the counts, so the
   trail says what was erased even though the rows are gone. Audit
   rows themselves are never purged (see the repository module).

Idempotent: purging an empty or never-created workspace returns zeros
and ``closed=False`` with 200, so an off-boarding job can retry safely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pyfly.container import service

from flycanon.core.services.audit import AuditService
from flycanon.core.services.events import WorkspaceEventPublisher
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.models.repositories import SourceRepository, WorkspaceRepository
from flycanon.models.repositories.scope_purge_repository import ScopePurgeRepository

logger = logging.getLogger(__name__)

#: Sources removed per page; each page is re-read from offset 0 because
#: every removal shrinks the scope.
_SOURCE_PAGE = 100


@dataclass(slots=True)
class PurgeReport:
    """What one purge erased. Mirrors the wire ``WorkspacePurgeResult``."""

    tenant_id: str
    workspace_id: str
    sources_removed: int = 0
    originals_deleted: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    closed: bool = False


@service
class WorkspacePurgeService:
    def __init__(
        self,
        intake: IntakeService,
        source_repository: SourceRepository,
        purge_repository: ScopePurgeRepository,
        workspace_repository: WorkspaceRepository,
        audit: AuditService,
        event_publisher: WorkspaceEventPublisher,
    ) -> None:
        self._intake = intake
        self._sources = source_repository
        self._purge = purge_repository
        self._workspaces = workspace_repository
        self._audit = audit
        self._events = event_publisher

    async def purge(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> PurgeReport:
        report = PurgeReport(tenant_id=tenant_id, workspace_id=workspace_id)

        # 1. Sources through the full removal pipeline.
        while True:
            rows, _total = await self._sources.list_sources(
                limit=_SOURCE_PAGE,
                offset=0,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if not rows:
                break
            for row in rows:
                had_original = bool(getattr(row, "object_store_key", None))
                await self._intake.remove(
                    source_id=row.id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor=actor,
                    correlation_id=correlation_id,
                )
                report.sources_removed += 1
                if had_original:
                    report.originals_deleted += 1

        # 2. Everything else in the scope.
        report.counts = await self._purge.purge_scope(tenant_id=tenant_id, workspace_id=workspace_id)

        # 3. Close the workspace row if there is one.
        report.closed = await self._workspaces.close(tenant_id, workspace_id)
        if report.closed:
            await self._events.publish_deleted(tenant_id=tenant_id, workspace_id=workspace_id)

        # 4. The trail.
        await self._audit.record(
            event_type="workspace.purged",
            subject_kind="workspace",
            subject_id=workspace_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "sources_removed": report.sources_removed,
                "originals_deleted": report.originals_deleted,
                "closed": report.closed,
                **report.counts,
            },
        )
        logger.info(
            "workspace purged tenant=%s workspace=%s sources=%d closed=%s counts=%s",
            tenant_id,
            workspace_id,
            report.sources_removed,
            report.closed,
            report.counts,
        )
        return report


__all__ = ["PurgeReport", "WorkspacePurgeService"]
