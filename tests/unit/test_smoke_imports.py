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
