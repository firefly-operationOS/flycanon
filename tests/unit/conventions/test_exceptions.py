# Copyright 2026 Firefly Software Solutions Inc
"""Exception class hierarchy used by handlers.py.

Every exception knows its own (status, code, type-URI). The
handler renders it to a ``ProblemDetail`` without per-exception
mapper code.
"""

from __future__ import annotations

import pytest

from flycanon.web.conventions.exceptions import (
    BudgetExceeded,
    FireflyHTTPException,
    IdempotencyKeyConflict,
    InvalidRequest,
    MissingTenantContext,
    ResourceNotFound,
    TenantClaimMismatch,
    WorkspaceNotFound,
)


def test_firefly_base_has_required_class_attrs() -> None:
    assert FireflyHTTPException.status == 0  # sentinel; concrete subclasses override
    assert FireflyHTTPException.code == ""
    assert FireflyHTTPException.title == ""


def test_missing_tenant_context_shape() -> None:
    exc = MissingTenantContext("X-Tenant-Id header is required.")
    assert exc.status == 400
    assert exc.code == "missing_tenant_context"
    assert exc.title == "Missing tenant context"
    assert exc.detail == "X-Tenant-Id header is required."
    assert exc.type_uri == "https://firefly.dev/problems/missing_tenant_context"


def test_tenant_claim_mismatch_shape() -> None:
    exc = TenantClaimMismatch("JWT tenant claim does not match X-Tenant-Id.")
    assert exc.status == 403
    assert exc.code == "tenant_claim_mismatch"


def test_workspace_not_found_shape() -> None:
    exc = WorkspaceNotFound("Workspace 'ws_q3' does not exist.")
    assert exc.status == 404
    assert exc.code == "workspace_not_found"


def test_resource_not_found_shape() -> None:
    exc = ResourceNotFound("Source 'src_42' does not exist.")
    assert exc.status == 404
    assert exc.code == "resource_not_found"


def test_invalid_request_carries_errors_array() -> None:
    exc = InvalidRequest(
        "Validation failed.",
        errors=[{"code": "missing", "path": "name", "message": "required"}],
    )
    assert exc.status == 422
    assert exc.code == "invalid_request"
    assert exc.errors == [{"code": "missing", "path": "name", "message": "required"}]


def test_budget_exceeded_shape() -> None:
    exc = BudgetExceeded("Monthly budget reached.")
    assert exc.status == 402
    assert exc.code == "budget_exceeded"


def test_idempotency_key_conflict_shape() -> None:
    exc = IdempotencyKeyConflict("Key reused with different payload.")
    assert exc.status == 409
    assert exc.code == "idempotency_key_conflict"


def test_unknown_subclass_must_override_status_and_code() -> None:
    class DummyConcreteWithoutOverride(FireflyHTTPException):
        pass

    with pytest.raises(AssertionError):
        DummyConcreteWithoutOverride("x")
