# Copyright 2026 Firefly Software Solutions Inc
"""Header-constant smoke tests.

Locking the spelling of every header in one place stops a typo
slipping into a controller and breaking the contract for the
whole product.
"""

from __future__ import annotations

from flycanon.web.conventions import headers


def test_tenant_header_name() -> None:
    assert headers.HEADER_TENANT_ID == "X-Tenant-Id"


def test_workspace_header_name() -> None:
    assert headers.HEADER_WORKSPACE_ID == "X-Workspace-Id"


def test_correlation_header_name() -> None:
    assert headers.HEADER_CORRELATION_ID == "X-Correlation-Id"


def test_idempotency_header_name() -> None:
    assert headers.HEADER_IDEMPOTENCY_KEY == "Idempotency-Key"


def test_agent_token_header_name() -> None:
    assert headers.HEADER_AGENT_TOKEN == "X-Agent-Token"


def test_authorization_header_name() -> None:
    assert headers.HEADER_AUTHORIZATION == "Authorization"
