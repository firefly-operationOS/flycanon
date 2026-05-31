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
