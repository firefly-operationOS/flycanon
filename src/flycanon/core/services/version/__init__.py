# Copyright 2026 Firefly Software Solutions Inc
"""``GetVersionInfoHandler`` -- /api/v1/version backing query."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flycanon import __version__
from flycanon.config import CanonSettings
from flycanon.interfaces.dtos import VersionInfo


@dataclass(frozen=True)
class GetVersionInfoQuery(Query[VersionInfo]):
    pass


@query_handler
@service
class GetVersionInfoHandler(QueryHandler[GetVersionInfoQuery, VersionInfo]):
    def __init__(self, settings: CanonSettings) -> None:
        super().__init__()
        self._settings = settings

    async def do_handle(self, query: GetVersionInfoQuery) -> VersionInfo:
        return VersionInfo(
            service="flycanon",
            version=__version__,
            embedding_model=self._settings.embedding_model,
            answer_model=self._settings.answer_model,
            answer_fallback_model=self._settings.answer_fallback_model or "",
            vector_store=self._settings.vector_store,
            eda_adapter=self._settings.eda_adapter,
        )


__all__ = ["GetVersionInfoHandler", "GetVersionInfoQuery"]
