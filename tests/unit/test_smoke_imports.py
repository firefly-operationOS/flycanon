# Copyright 2026 Firefly Software Solutions Inc
"""Top-level smoke tests.

These exist to catch the failure mode where a refactor breaks
``import`` for an entire package -- the actual unit tests assume
the imports work, so a broken import surface manifests as
collection errors rather than test failures.
"""

from __future__ import annotations


def test_top_level_imports() -> None:
    """Every major flycanon package imports cleanly."""
    import flycanon  # noqa: F401
    import flycanon.core  # noqa: F401
    import flycanon.interfaces.dtos  # noqa: F401
    import flycanon.interfaces.enums  # noqa: F401
    import flycanon.models.entities  # noqa: F401
    import flycanon.models.repositories  # noqa: F401
    import flycanon.web  # noqa: F401


def test_conventions_module_imports() -> None:
    """The new flycanon.web.conventions module is fully wired."""
    from flycanon.web import conventions

    assert conventions.TenantContext.__name__ == "TenantContext"
    assert conventions.ProblemDetail.__name__ == "ProblemDetail"
    assert conventions.FireflyHTTPException.__name__ == "FireflyHTTPException"
    assert callable(conventions.require_tenant_context)
    assert callable(conventions.register_exception_handlers)
    assert callable(conventions.tenant_safe_client)
    assert conventions.HEADER_TENANT_ID == "X-Tenant-Id"
    assert conventions.HEADER_WORKSPACE_ID == "X-Workspace-Id"
