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

"""Public-API smoke test.

Asserts the surface the rest of flyradar (and the mirror flycanon
module) imports from. Anything not re-exported here is private.
"""

from __future__ import annotations


def test_public_exports() -> None:
    import flycanon.web.conventions as c

    assert c.HEADER_TENANT_ID == "X-Tenant-Id"
    assert c.HEADER_WORKSPACE_ID == "X-Workspace-Id"
    assert c.HEADER_CORRELATION_ID == "X-Correlation-Id"
    assert c.HEADER_IDEMPOTENCY_KEY == "Idempotency-Key"
    assert c.HEADER_AGENT_TOKEN == "X-Agent-Token"
    assert c.HEADER_AUTHORIZATION == "Authorization"

    assert c.TenantContext.__name__ == "TenantContext"
    assert callable(c.current_tenant_context)
    assert callable(c.set_tenant_context)
    assert callable(c.require_tenant_context)
    assert callable(c.register_exception_handlers)
    assert callable(c.tenant_safe_client)
    assert callable(c.validate_slug)

    assert c.ProblemDetail.__name__ == "ProblemDetail"
    assert c.FireflyHTTPException.__name__ == "FireflyHTTPException"
    assert c.MissingTenantContext.__name__ == "MissingTenantContext"
    assert c.TenantClaimMismatch.__name__ == "TenantClaimMismatch"
    assert c.WorkspaceNotFound.__name__ == "WorkspaceNotFound"
    assert c.ResourceNotFound.__name__ == "ResourceNotFound"
    assert c.InvalidRequest.__name__ == "InvalidRequest"
    assert c.BudgetExceeded.__name__ == "BudgetExceeded"
    assert c.IdempotencyKeyConflict.__name__ == "IdempotencyKeyConflict"
    assert c.IdempotencyKey.__name__ == "IdempotencyKey"
    assert c.IdempotencyStore.__name__ == "IdempotencyStore"
    assert c.InMemoryIdempotencyStore.__name__ == "InMemoryIdempotencyStore"
    assert c.MissingIdempotencyKey.__name__ == "MissingIdempotencyKey"
    # Added 2026-05-22 alongside StoredResponse for agent-tier
    # replay dedup -- the agent controllers cache responses under
    # the (tenant, scope, key) triple.
    assert c.StoredResponse.__name__ == "StoredResponse"
    assert c.Actor.__name__ == "Actor"
    assert callable(c.actor_from_jwt_claims)
    assert callable(c.actor_from_agent_token)


def test_all_lists_every_export_and_size_locked() -> None:
    """Lock the surface size; any addition/removal must update the list.

    Iterating over ``__all__`` catches the case where a name lands in
    the list but no symbol is re-exported (and vice versa) -- a typo
    that would otherwise pass the by-name asserts above.
    """
    import flycanon.web.conventions as c

    assert len(c.__all__) == 38
    for name in c.__all__:
        assert hasattr(c, name), f"__all__ lists {name!r} but module has no such attribute"
    # Newest addition (2026-05-22): TenantContextMiddleware -- pyfly's
    # @rest_controller bypasses FastAPI Depends, so the canonical
    # require_tenant_context yield-dep never runs. The middleware
    # closes that gap by binding the ContextVar from headers before
    # any DB session opens. See ``web/conventions/middleware.py``.
    assert "TenantContextMiddleware" in c.__all__
    assert c.TenantContextMiddleware.__name__ == "TenantContextMiddleware"
