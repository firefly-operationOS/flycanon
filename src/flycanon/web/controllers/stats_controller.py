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

"""Corpus / queue / cost inventory endpoint under ``/api/v1/stats``."""

from __future__ import annotations

import logging
from datetime import datetime

from pyfly.container import rest_controller
from pyfly.web import get_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.stats import StatsService
from flycanon.interfaces.dtos.stats import (
    CandidateStats,
    ChunkStats,
    CorpusStats,
    CostStreamStats,
    JobStats,
    KnowledgeItemStats,
    SourceStats,
)
from flycanon.web.conventions import TenantContext, tenant_context_from_request

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/stats")
class StatsController:
    """One-shot inventory of the canon.

    Aggregates corpus counts (sources / knowledge items / versions /
    candidates / chunks), ingest-queue health, and a headline cost
    rollup so a status dashboard can render the whole picture in one
    HTTP round-trip. Detailed cost drill-downs live under
    ``/api/v1/billing/*``.

    Scope is enforced via the standard request headers, even though
    the v1 snapshot itself is currently global -- per-workspace
    rollups land in a follow-up so the snapshot wiring already
    enforces the tenant contract.
    """

    def __init__(self, stats: StatsService) -> None:
        self._stats = stats

    @get_mapping("")
    async def get(self, http_request: Request) -> CorpusStats:
        _ctx: TenantContext = tenant_context_from_request(http_request)
        snapshot = await self._stats.snapshot()
        return CorpusStats(
            generated_at=datetime.fromisoformat(snapshot["generated_at"]),
            sources=SourceStats(**snapshot["sources"]),
            knowledge_items=KnowledgeItemStats(**snapshot["knowledge_items"]),
            knowledge_versions=int(snapshot["knowledge_versions"]),
            candidates=CandidateStats(**snapshot["candidates"]),
            chunks=ChunkStats(**snapshot["chunks"]),
            ingest_jobs=JobStats(**snapshot["ingest_jobs"]),
            cost=CostStreamStats(**snapshot["cost"]),
        )
