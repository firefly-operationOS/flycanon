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
