# Copyright 2026 Firefly Software Foundation
# SPDX-License-Identifier: Apache-2.0
"""Expiry reaches HTTP as JSON while retaining its timezone and scoped grants."""

import json
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import httpx
import pytest
import respx

from flycanon_sdk import AgentTokenMintRequest, CanonClient


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", [UTC, timezone(timedelta(hours=5, minutes=30))])
async def test_mint_expiring_token_serializes_datetime_at_http_boundary(zone: tzinfo) -> None:
    expiry = datetime(2031, 1, 2, 3, 4, 5, tzinfo=zone)
    received = []

    def serve(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["X-Tenant-Id"] == "expiry-test"
        assert request.headers["X-Workspace-Id"] == "workspace-test"
        assert isinstance(body["expires_at"], str)
        assert datetime.fromisoformat(body["expires_at"]) == expiry
        assert body["scopes"] == ["agent.sources:read"]
        assert body["workspace_allowlist"] == ["workspace-test"]
        received.append(body)
        return httpx.Response(
            201,
            json={
                "id": "token-test",
                "name": body["name"],
                "prefix": "test_prefix",
                "workspace_allowlist": body["workspace_allowlist"],
                "scopes": body["scopes"],
                "rate_limit_rpm": 20,
                "expires_at": body["expires_at"],
                "created_at": "2030-01-01T00:00:00Z",
                "created_by": "fixture",
                "token": "fictional-token",
                "revoked_at": None,
                "last_used_at": None,
            },
        )

    async with (
        respx.mock(base_url="http://canon") as mock,
        CanonClient(
            base_url="http://canon", tenant_id="expiry-test", workspace_id="workspace-test"
        ) as client,
    ):
        mock.post("/api/v1/agent-tokens").mock(side_effect=serve)
        result = await client.mint_agent_token(
            AgentTokenMintRequest(
                name="bounded-expiry",
                workspace_allowlist=["workspace-test"],
                scopes=["agent.sources:read"],
                rate_limit_rpm=20,
                expires_at=expiry,
            )
        )
    assert len(received) == 1
    assert result.expires_at == expiry
