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

"""The OpenAPI document declares the headers and credentials a client must send.

Two layers: the pure :func:`apply_wire_contract` transform on a
hand-built spec (fast, no app boot), and assertions over the committed
``openapi.json`` snapshot so a regenerated document that lost the
contract fails here as well as in the byte-for-byte snapshot gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from flycanon.web.openapi_override import (
    HEADER_PARAMETERS,
    SECURITY_SCHEMES,
    apply_wire_contract,
)

_TENANT_REFS = {
    "#/components/parameters/XTenantId",
    "#/components/parameters/XWorkspaceId",
    "#/components/parameters/XCorrelationId",
}
_IDEMPOTENCY_REF = "#/components/parameters/IdempotencyKey"


def _refs(operation: dict) -> set[str]:
    return {p["$ref"] for p in operation.get("parameters", []) if "$ref" in p}


def _sample_spec() -> dict:
    return {
        "paths": {
            "/api/v1/version": {"get": {"operationId": "version"}},
            "/api/v1/sources": {
                "post": {"operationId": "submit", "parameters": [{"in": "query", "name": "mode"}]},
                "get": {"operationId": "list"},
            },
            "/api/v1/agent/sources/{source_id}": {
                "delete": {"operationId": "remove", "parameters": [{"in": "path", "name": "source_id"}]},
                "get": {"operationId": "get"},
            },
        }
    }


class TestApplyWireContract:
    def test_components_are_declared(self) -> None:
        spec = apply_wire_contract(_sample_spec())
        assert spec["components"]["securitySchemes"] == SECURITY_SCHEMES
        assert spec["components"]["parameters"] == HEADER_PARAMETERS
        assert SECURITY_SCHEMES["ApiKeyHeader"]["name"] == "X-API-Key"
        assert SECURITY_SCHEMES["AgentToken"]["name"] == "X-Agent-Token"
        assert HEADER_PARAMETERS["XTenantId"]["required"] is True
        assert HEADER_PARAMETERS["XWorkspaceId"]["required"] is True
        assert HEADER_PARAMETERS["XCorrelationId"]["required"] is False

    def test_version_stays_public(self) -> None:
        spec = apply_wire_contract(_sample_spec())
        op = spec["paths"]["/api/v1/version"]["get"]
        assert "security" not in op and "parameters" not in op

    def test_user_tier_gets_platform_key_and_tenant_headers(self) -> None:
        spec = apply_wire_contract(_sample_spec())
        post = spec["paths"]["/api/v1/sources"]["post"]
        assert post["security"] == [{"ApiKeyHeader": []}, {"ApiKeyAuthorization": []}]
        assert _refs(post) == _TENANT_REFS
        # Existing query parameters are preserved.
        assert {"in": "query", "name": "mode"} in post["parameters"]

    def test_agent_tier_gets_token_alternative_and_idempotency_on_mutations(self) -> None:
        spec = apply_wire_contract(_sample_spec())
        delete = spec["paths"]["/api/v1/agent/sources/{source_id}"]["delete"]
        get = spec["paths"]["/api/v1/agent/sources/{source_id}"]["get"]
        # The agent token is the ONLY alternative: a platform key never
        # satisfies an agent operation (the route answers
        # ``401 missing_agent_token``), so listing it would mislead a
        # generated client.
        assert delete["security"] == [{"AgentToken": []}]
        assert get["security"] == [{"AgentToken": []}]
        assert _refs(delete) == _TENANT_REFS | {_IDEMPOTENCY_REF}
        assert _refs(get) == _TENANT_REFS

    def test_idempotent_on_repeated_application(self) -> None:
        spec = apply_wire_contract(apply_wire_contract(_sample_spec()))
        post = spec["paths"]["/api/v1/sources"]["post"]
        assert len([p for p in post["parameters"] if "$ref" in p]) == 3


class TestCommittedSnapshot:
    def _spec(self) -> dict:
        return json.loads((Path(__file__).resolve().parents[2] / "openapi.json").read_text())

    def test_snapshot_declares_the_contract_on_every_tenant_operation(self) -> None:
        spec = self._spec()
        assert set(spec["components"]["securitySchemes"]) == set(SECURITY_SCHEMES)
        for path, operations in spec["paths"].items():
            for method, op in operations.items():
                if (method, path) == ("get", "/api/v1/version"):
                    assert "security" not in op
                    continue
                assert op.get("security"), f"{method.upper()} {path} has no security requirement"
                assert _refs(op) >= _TENANT_REFS, f"{method.upper()} {path} misses tenant headers"
                if path.startswith("/api/v1/agent/"):
                    assert op["security"] == [{"AgentToken": []}], f"{method.upper()} {path}"
                    if method in {"post", "put", "delete"}:
                        assert _IDEMPOTENCY_REF in _refs(op), (
                            f"{method.upper()} {path} misses Idempotency-Key"
                        )
                else:
                    assert op["security"] == [{"ApiKeyHeader": []}, {"ApiKeyAuthorization": []}], (
                        f"{method.upper()} {path}"
                    )

    def test_snapshot_carries_the_new_verbs(self) -> None:
        spec = self._spec()
        assert "delete" in spec["paths"]["/api/v1/sources/{source_id}"]
        assert "post" in spec["paths"]["/api/v1/workspaces/{workspace_id}:purge"]
        stream = spec["paths"]["/api/v1/ingest-jobs/{job_id}/stream"]["get"]
        assert any(p.get("name") == "after_id" for p in stream["parameters"])
        assert spec["info"]["version"] == "26.7.1"
