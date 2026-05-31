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
    """Identity + model information for the running flycanon instance.

    Surfaces the service slug, CalVer build tag, the embedding +
    answer-model identifiers actually used by the retrieval +
    consolidation stages, the selected vector backend, and the EDA
    adapter. Useful for:

    * smoke tests (``task version`` in the Taskfile points here),
    * operations dashboards that compare deployed models across
      environments,
    * answering the "which model is this hitting?" question on the
      operations channel without SSH-ing into a pod.

    No authentication required by default; gate via
    ``FLYCANON_API_KEYS`` when fronting the service with a public
    LB.
    """

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("/version")
    async def version(self) -> VersionInfo:
        """Return the service identity + provider selection.

        Response shape (:class:`VersionInfo`):

        * ``service`` -- always ``"flycanon"``.
        * ``version`` -- CalVer baked into the wheel (``YY.MM.PP``,
          e.g. ``"26.5.1"``).
        * ``embedding_model`` / ``answer_model`` /
          ``answer_fallback_model`` -- ``<provider>:<model>``
          identifiers read from :class:`CanonSettings`.
        * ``vector_store`` -- ``pgvector``.
        * ``eda_adapter`` -- one of ``postgres`` (default),
          ``memory``, ``redis``, ``kafka``.
        """
        return await self._queries.query(GetVersionInfoQuery())
