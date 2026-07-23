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

"""``RemoveSourceHandler`` -- delete a source and its projections."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler

from flycanon.core.services.sources.intake_service import IntakeService


@dataclass(frozen=True)
class RemoveSourceCommand(Command[None]):
    """Remove a source by id within the caller's scope.

    ``tenant_id`` and ``workspace_id`` are required -- callers must
    propagate the request-bound :class:`TenantContext` rather than
    relying on a silent ``"default"`` fallback. A missing scope is a
    controller bug and surfaces as a TypeError on construction.
    """

    source_id: str
    tenant_id: str
    workspace_id: str
    actor: str | None = None
    correlation_id: str | None = None


@command_handler
@service
class RemoveSourceHandler(CommandHandler[RemoveSourceCommand, None]):
    def __init__(self, intake: IntakeService) -> None:
        super().__init__()
        self._intake = intake

    async def do_handle(self, command: RemoveSourceCommand) -> None:
        await self._intake.remove(
            source_id=command.source_id,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            actor=command.actor,
            correlation_id=command.correlation_id,
        )
