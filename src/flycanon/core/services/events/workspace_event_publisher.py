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

"""``WorkspaceEventPublisher`` -- emits workspace lifecycle events.

The publisher is a thin facade over pyfly's
:class:`pyfly.eda.EventPublisher`. Every successful workspace
mutation in :class:`flycanon.web.controllers.workspaces_controller.WorkspacesController`
calls one of the ``publish_*`` methods; the publisher translates the
post-mutation row into the matching event DTO and pushes it onto
topic ``canon.workspaces.v1``.

**Backend**: pyfly's :class:`EventPublisher` bean. The same auto-
configured publisher used by :class:`AuditService`,
:class:`KnowledgeService`, etc. Whichever transport pyfly is wired
to (Postgres adapter by default, see ``FLYCANON_EDA_ADAPTER``) ends
up carrying these events too. Production transport tuning (Kafka,
RabbitMQ) is OUT OF SCOPE for this plan -- swap the
``FLYCANON_EDA_ADAPTER`` env var when the time comes.

**Consistency**: best-effort. A repository write succeeds first;
only then does the controller call us. If the EDA publish itself
fails the failure is logged and swallowed -- downstream consumers
can rebuild from ``canon_workspaces`` or replay from ``canon_audit_events``.
A transactional outbox is OUT OF SCOPE for this plan; the durable
truth is the workspace row in Postgres.

**Listeners**: tests attach in-process listeners via :meth:`add_listener`
to assert on the emitted DTOs without involving pyfly's transport.
The fan-out happens *in addition to* the pyfly publish, so production
behaviour is unchanged when no listener is attached.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pyfly.container import service
from pyfly.eda import EventPublisher

from flycanon.config import CanonSettings
from flycanon.interfaces.dtos.workspace_event import (
    WorkspaceCreated,
    WorkspaceDeleted,
    WorkspaceEventBase,
    WorkspaceUpdated,
)

logger = logging.getLogger(__name__)

WorkspaceEventListener = Callable[[WorkspaceEventBase], Awaitable[None]]


@service
class WorkspaceEventPublisher:
    """Emits workspace lifecycle events on topic ``canon.workspaces.v1``.

    Marked ``@service`` so pyfly's container auto-discovers it via the
    standard ``scan_packages`` path. Constructor-injects the pyfly
    :class:`EventPublisher` and the :class:`CanonSettings` (which
    carry the topic + event-type names).
    """

    def __init__(
        self,
        event_publisher: EventPublisher,
        settings: CanonSettings,
    ) -> None:
        self._publisher = event_publisher
        self._settings = settings
        self._listeners: list[WorkspaceEventListener] = []

    # ------------------------------------------------------------------
    # Public publish surface
    # ------------------------------------------------------------------

    async def publish_created(self, workspace: dict[str, Any]) -> None:
        """Emit a :class:`WorkspaceCreated` event.

        ``workspace`` is the post-insert row dict returned by
        :meth:`flycanon.models.repositories.workspace_repository.WorkspaceRepository.get`
        -- same shape that ``_to_spec`` consumes in the controller.
        """
        event = WorkspaceCreated(
            tenant_id=workspace["tenant_id"],
            workspace_id=workspace["id"],
            occurred_at=datetime.now(UTC),
            name=workspace["name"],
            scope=workspace.get("scope_json"),
            sme_roster=workspace.get("sme_roster_json"),
            retention_days=workspace.get("retention_days"),
            jurisdiction=workspace.get("jurisdiction"),
        )
        await self._dispatch(event, self._settings.workspace_created_event)

    async def publish_updated(self, workspace: dict[str, Any]) -> None:
        """Emit a :class:`WorkspaceUpdated` event.

        ``workspace`` is the post-patch row dict. The event carries
        the whole updated row state (not just the patched fields) so
        consumers can replace their cached row wholesale.
        """
        event = WorkspaceUpdated(
            tenant_id=workspace["tenant_id"],
            workspace_id=workspace["id"],
            occurred_at=datetime.now(UTC),
            name=workspace["name"],
            scope=workspace.get("scope_json"),
            sme_roster=workspace.get("sme_roster_json"),
            retention_days=workspace.get("retention_days"),
            jurisdiction=workspace.get("jurisdiction"),
        )
        await self._dispatch(event, self._settings.workspace_updated_event)

    async def publish_deleted(self, *, tenant_id: str, workspace_id: str) -> None:
        """Emit a :class:`WorkspaceDeleted` event.

        flycanon's ``close`` route is the terminal lifecycle
        transition; downstream consumers see this as the "delete"
        signal. The (tenant_id, workspace_id) pair is enough for the
        consumer to drop or tombstone its cached row.
        """
        event = WorkspaceDeleted(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            occurred_at=datetime.now(UTC),
        )
        await self._dispatch(event, self._settings.workspace_deleted_event)

    # ------------------------------------------------------------------
    # Test hooks
    # ------------------------------------------------------------------

    def add_listener(self, listener: WorkspaceEventListener) -> None:
        """Attach an in-process listener.

        Used by tests to assert on emitted DTOs without involving
        pyfly's transport. Production code never calls this -- the
        EDA bus is the integration seam.
        """
        self._listeners.append(listener)

    def clear_listeners(self) -> None:
        """Drop every attached listener -- typically called in a test teardown."""
        self._listeners.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _dispatch(self, event: WorkspaceEventBase, event_type_name: str) -> None:
        """Publish ``event`` on the workspace topic + fan-out to listeners.

        EDA failures are logged and swallowed (best-effort
        consistency). Listener failures bubble up so tests see them
        loudly.
        """
        await self._publish_to_bus(event, event_type_name)
        for listener in list(self._listeners):
            await listener(event)

    async def _publish_to_bus(
        self,
        event: WorkspaceEventBase,
        event_type_name: str,
    ) -> None:
        if self._publisher is None:
            return
        try:
            await self._publisher.publish(  # type: ignore[attr-defined]
                destination=self._settings.workspace_topic,
                event_type=event_type_name,
                payload=event.model_dump(mode="json"),
            )
        except Exception as exc:
            # Log + swallow: the durable trail is in ``canon_workspaces``
            # (and the parallel ``canon_audit_events`` row). The EDA
            # bus is best-effort and consumers can rebuild from those
            # tables when their projection is behind.
            logger.warning(
                "workspace event publish failed event_type=%s workspace_id=%s: %s",
                event_type_name,
                event.workspace_id,
                exc,
            )
