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

"""Audit-log read endpoint under ``/api/v1/audit``."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import QueryParam, get_mapping, request_mapping
from starlette.requests import Request

from flycanon.core.services.audit.handlers import ListAuditQuery
from flycanon.interfaces.dtos.audit import AuditPage
from flycanon.web.conventions import TenantContext, tenant_context_from_request


@rest_controller
@request_mapping("/api/v1/audit")
class AuditController:
    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @get_mapping("")
    async def list_audit(
        self,
        http_request: Request,
        subject_id: QueryParam[str] = "",
        subject_kind: QueryParam[str] = "",
        event_type: QueryParam[str] = "",
        limit: QueryParam[int] = 50,
        offset: QueryParam[int] = 0,
    ) -> AuditPage:
        """Paginated audit-log view -- filters compose with AND.

        Scope is fixed to the (tenant, workspace) carried by the
        request headers; cross-scope audit views are out of v1.
        """
        ctx: TenantContext = tenant_context_from_request(http_request)
        return await self._queries.query(
            ListAuditQuery(
                subject_id=subject_id or None,
                subject_kind=subject_kind or None,
                event_type=event_type or None,
                limit=limit,
                offset=offset,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
