# Copyright 2026 Firefly Software Solutions Inc
"""Identity + model info -- ``GET /api/v1/version``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import get_mapping, request_mapping

from flycanon.core.services.version import GetVersionInfoQuery
from flycanon.interfaces.dtos import VersionInfo


@rest_controller
@request_mapping("/api/v1")
class VersionController:
    """Identity + model information for the running instance."""

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("/version", tags=["Version"])
    async def version(self) -> VersionInfo:
        """Return the service identity, embedding + answer model, and
        retrieval / EDA backend selection."""
        return await self._queries.query(GetVersionInfoQuery())
