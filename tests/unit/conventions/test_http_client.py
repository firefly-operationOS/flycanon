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

"""``tenant_safe_client`` auto-injects tenant/workspace/correlation
headers on outbound calls.

Use case: an agent endpoint calling flycanon needs its own
``(tenant, workspace)`` propagated. Forgetting to thread them
through is the #1 way multi-tenancy plumbing leaks. The client
reads ``current_tenant_context()`` and adds the headers; if the
context is unbound, it refuses to send (fail-closed).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from flycanon.web.conventions.context import (
    TenantContext,
    set_tenant_context,
)
from flycanon.web.conventions.http_client import (
    MissingOutboundContextError,
    tenant_safe_client,
)


@pytest.mark.asyncio
@respx.mock
async def test_outbound_request_carries_tenant_headers() -> None:
    set_tenant_context(
        TenantContext(
            tenant_id="acme",
            workspace_id="ws-q3",
            actor="user:alice",
            correlation_id="01J5XYZ",
        )
    )
    route = respx.get("https://flycanon.example/api/v1/version").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with tenant_safe_client(base_url="https://flycanon.example") as client:
        response = await client.get("/api/v1/version")

    assert response.status_code == 200
    assert route.called
    request_headers = route.calls.last.request.headers
    assert request_headers["X-Tenant-Id"] == "acme"
    assert request_headers["X-Workspace-Id"] == "ws-q3"
    assert request_headers["X-Correlation-Id"] == "01J5XYZ"


@pytest.mark.asyncio
async def test_outbound_request_fails_closed_without_context() -> None:
    # No set_tenant_context() call here.
    async with tenant_safe_client(base_url="https://flycanon.example") as client:
        with pytest.raises(MissingOutboundContextError):
            await client.get("/api/v1/version")


@pytest.mark.asyncio
@respx.mock
async def test_caller_supplied_headers_not_overridden() -> None:
    set_tenant_context(
        TenantContext(
            tenant_id="acme",
            workspace_id="ws",
            correlation_id="ctx-corr",
        )
    )
    route = respx.get("https://flycanon.example/x").mock(return_value=httpx.Response(200))

    async with tenant_safe_client(base_url="https://flycanon.example") as client:
        await client.get("/x", headers={"X-Correlation-Id": "caller-corr"})

    assert route.called
    headers = route.calls.last.request.headers
    # Caller-supplied correlation wins.
    assert headers["X-Correlation-Id"] == "caller-corr"
    # Tenant/workspace are still injected.
    assert headers["X-Tenant-Id"] == "acme"
    assert headers["X-Workspace-Id"] == "ws"
