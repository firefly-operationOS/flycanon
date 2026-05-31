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

"""``ReplaceSourceHandler`` -- re-ingest under the same source_id."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyfly.container import service
from pyfly.cqrs import Command, CommandHandler, command_handler

from flycanon.core.mappers import to_source_record
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.interfaces.dtos.source import SourceMetadata, SourceRecord, SubmitSourceRequest
from flycanon.interfaces.enums import SourceKind


@dataclass(frozen=True)
class ReplaceSourceCommand(Command[SourceRecord]):
    """Re-ingest a source preserving the existing ``source_id``.

    Same payload shape as :class:`SubmitSourceCommand` plus an
    explicit ``source_id`` targeting the existing row. Citations
    referencing chunks that disappear under the new emission are
    not deleted (v1) -- they surface as ``dangling_citation`` audit
    warnings.

    ``tenant_id`` and ``workspace_id`` are required -- callers must
    propagate the request-bound :class:`TenantContext` rather than
    relying on a silent ``"default"`` fallback. A missing scope is a
    controller bug and surfaces as a TypeError on construction.
    """

    source_id: str
    content: bytes
    tenant_id: str
    workspace_id: str
    metadata: SourceMetadata = field(default_factory=SourceMetadata)
    filename: str | None = None
    content_type: str | None = None
    kind: SourceKind = SourceKind.unknown
    uri: str | None = None
    actor: str | None = None
    correlation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@command_handler
@service
class ReplaceSourceHandler(CommandHandler[ReplaceSourceCommand, SourceRecord]):
    def __init__(self, intake: IntakeService) -> None:
        super().__init__()
        self._intake = intake

    async def do_handle(self, command: ReplaceSourceCommand) -> SourceRecord:
        request = SubmitSourceRequest(
            kind=command.kind,
            uri=command.uri,
            metadata=command.metadata,
        )
        source = await self._intake.replace(
            source_id=command.source_id,
            request=request,
            content=command.content,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            filename=command.filename,
            content_type=command.content_type,
            actor=command.actor,
            correlation_id=command.correlation_id,
        )
        return to_source_record(source)
