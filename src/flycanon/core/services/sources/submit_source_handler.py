# Copyright 2026 Firefly Software Solutions Inc
"""``SubmitSourceHandler`` -- the CQRS entry for source intake."""

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
class SubmitSourceCommand(Command[SourceRecord]):
    """Submit a source for intake.

    ``content`` carries the raw bytes (already decoded from multipart
    by the controller). ``metadata`` is the caller-supplied
    SourceMetadata; the rest of the fields are optional hints used
    when the controller can't fingerprint the source on its own.
    """

    content: bytes
    metadata: SourceMetadata = field(default_factory=SourceMetadata)
    filename: str | None = None
    content_type: str | None = None
    kind: SourceKind = SourceKind.unknown
    uri: str | None = None
    actor: str | None = None
    correlation_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@command_handler
@service
class SubmitSourceHandler(CommandHandler[SubmitSourceCommand, SourceRecord]):
    def __init__(self, intake: IntakeService) -> None:
        super().__init__()
        self._intake = intake

    async def do_handle(self, command: SubmitSourceCommand) -> SourceRecord:
        request = SubmitSourceRequest(
            kind=command.kind,
            uri=command.uri,
            metadata=command.metadata,
        )
        source = await self._intake.submit(
            request=request,
            content=command.content,
            filename=command.filename,
            content_type=command.content_type,
            actor=command.actor,
            correlation_id=command.correlation_id,
        )
        return to_source_record(source)
