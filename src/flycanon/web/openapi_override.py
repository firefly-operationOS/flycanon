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

"""Replace FastAPI's auto-generated OpenAPI with pyfly's richer schema.

The pyfly FastAPI adapter registers every controller method behind a
single ``lazy_endpoint(request: Request)`` shim so the DI container can
resolve the controller bean on first hit. The side-effect is that
FastAPI's built-in OpenAPI introspector sees only that shim -- no
request body, no response model, no tags, no docstring.

This module bridges the gap. After the FastAPI app is built we install
a custom ``app.openapi`` callable that:

1. Collects per-route metadata from the original controller signatures
   via pyfly's :class:`ControllerRegistrar.collect_route_metadata`.
2. Renders the spec through pyfly's :class:`OpenAPIGenerator`.
3. Enriches the result with rich global tags (with business +
   technical descriptions) and the OpenAPI ``info`` block we want
   Swagger / ReDoc to display.
4. Declares the wire contract pyfly's introspector cannot see because
   it lives in ``Request.headers`` reads rather than in signatures:
   the ``securitySchemes`` (platform API key in two header forms,
   agent token) and the mandatory / optional headers (``X-Tenant-Id``,
   ``X-Workspace-Id``, ``X-Correlation-Id``, ``Idempotency-Key``,
   ``X-Agent-Token``) attached per operation. Until 26.7.1 the
   document listed none of them, so a client generated from it
   (a PHP control plane, for instance) sent bare requests and got
   ``400 missing_tenant_context`` on its first call. The rules are
   in :func:`_security_for` / :func:`_header_parameters_for`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pyfly.context.application_context import ApplicationContext
from pyfly.web.adapters.starlette.controller import ControllerRegistrar
from pyfly.web.openapi import OpenAPIGenerator

logger = logging.getLogger(__name__)


#: Per-tag descriptions rendered on the Swagger / ReDoc landing page.
#: Each entry mixes the business intent with the technical contract so
#: the docs read like a runbook, not just a wire reference.
TAG_DESCRIPTIONS: dict[str, str] = {
    "Sources": (
        "Source intake. The front door for binary content -- DOCX, "
        "XLSX, PPTX, PDF, RTF, ODT/ODS/ODP, HTML, Markdown, plain "
        "text, CSV, TSV, JSON, XML, EPUB, raster images (PNG, JPEG, "
        "GIF, WebP, HEIC, AVIF, TIFF, SVG, BMP) routed through "
        "Tesseract OCR, ZIP / 7Z / TAR / GZ archives expanded inline, "
        "EML / MSG emails decomposed into body + attachments, and "
        "WebVTT / SRT transcripts. The intake pipeline sniffs the "
        "actual media type from magic bytes, runs the binary "
        "normaliser, loads through the per-format SourceLoader, "
        "chunks, embeds, and indexes BM25 + dense vectors keyed by "
        "chunk_id. SHA-256 hashing makes ingestion idempotent on "
        "content."
    ),
    "Knowledge": (
        "Canonical knowledge items. The validated, versioned units "
        "downstream consumers should treat as ground truth. Every "
        "create / update appends a new version row; supersession and "
        "retirement are final transitions. Every lifecycle event is "
        "audited and broadcast on the ``flycanon.knowledge`` topic, "
        "so projections (dashboards, copilots, compliance feeds) "
        "stay in lock-step without polling."
    ),
    "Candidates": (
        "Pre-canonical knowledge proposals. The consolidation stage "
        "feeds source chunks to an LLM (FireflyAgent over "
        "pydantic-ai) that emits structured "
        "CandidateProposals with chunk-anchored citations and a "
        "self-rated confidence score. Operators (or an automated "
        "policy) accept proposals to materialise a new knowledge "
        "version, reject them with a reason, or merge them into "
        "existing items. Every decision flows through the audit log."
    ),
    "Query": (
        "Hybrid search + grounded retrieval-augmented answering. "
        "``/search`` returns the raw RRF-fused hit list (BM25 + "
        "dense vectors). ``/query`` runs the same retrieval, then "
        "asks the configured answer model to write an answer using "
        "ONLY the retrieved chunks -- citations include only the "
        "chunks the model actually relied on. Both surfaces share "
        "the same filter model (source_id, knowledge_item_id, "
        "domain, jurisdiction, tags, statuses)."
    ),
    "Taxonomy": (
        "Domain + jurisdiction taxonomy. Seed inserts one root per "
        ":class:`Domain` value at first boot; callers attach "
        "finer-grained children at runtime (sub-processes, "
        "jurisdiction sub-trees). The tree is read flat in "
        "breadth-first order via the ``depth`` column so the API "
        "round-trip is index-only."
    ),
    "Audit": (
        "Append-only audit log. Every mutation in flycanon "
        "(``source.ingested``, ``knowledge.published``, "
        "``candidate.accepted``, ...) writes a row here with the "
        "actor, the correlation id from the originating request, "
        "and a free-form payload. The same payload is broadcast on "
        "the ``flycanon.audit`` topic for compliance projections."
    ),
    "Version": (
        "Service identity, model selection, and backend choices. "
        "Surfaces the deployed CalVer, the embedding + answer model "
        "ids actually in use, the vector backend (pgvector by "
        "default), and the EDA adapter. Used by smoke tests and "
        "operations dashboards."
    ),
}


#: Operations that need neither tenant headers nor a credential.
PUBLIC_OPERATIONS: frozenset[tuple[str, str]] = frozenset({("get", "/api/v1/version")})
AGENT_PREFIX = "/api/v1/agent/"
_MUTATING = frozenset({"post", "put", "patch", "delete"})

SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    "ApiKeyHeader": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": (
            "Platform API key from ``FLYCANON_API_KEYS``. Required on every "
            "``/api/v1/*`` route (except ``GET /api/v1/version``) and on the "
            "admin dashboard whenever at least one key is configured; when "
            "the setting is empty the user tier is open and the header is "
            "ignored. Missing -> ``401 missing_api_key``; unknown -> "
            "``401 invalid_api_key``."
        ),
    },
    "ApiKeyAuthorization": {
        "type": "http",
        "scheme": "ApiKey",
        "description": (
            "The same platform key carried as ``Authorization: ApiKey <key>`` "
            "for clients that can only set the Authorization header. "
            "``X-API-Key`` wins when both are present."
        ),
    },
    "AgentToken": {
        "type": "apiKey",
        "in": "header",
        "name": "X-Agent-Token",
        "description": (
            "Tenant-scoped agent token (``agt_<8hex>_<32hex>``) minted by "
            "``POST /api/v1/agent-tokens``. The credential of the "
            "``/api/v1/agent/*`` tier: verified per request against the "
            "tenant in ``X-Tenant-Id``, the workspace allowlist and the "
            "route scope. An agent-tier request carrying it does not need "
            "the platform key."
        ),
    },
}

#: Reusable header parameters (``#/components/parameters/<name>``).
HEADER_PARAMETERS: dict[str, dict[str, Any]] = {
    "XTenantId": {
        "name": "X-Tenant-Id",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"},
        "description": (
            "Tenant slug that scopes every row the request may see or write. "
            "Missing or malformed -> ``400 missing_tenant_context``."
        ),
    },
    "XWorkspaceId": {
        "name": "X-Workspace-Id",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"},
        "description": (
            "Workspace slug within the tenant. Required on every tenant route "
            "for header uniformity, including the workspace CRUD routes where "
            "the path id is authoritative (``:purge`` demands they agree)."
        ),
    },
    "XCorrelationId": {
        "name": "X-Correlation-Id",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "Caller-supplied correlation id echoed into audit rows, cost "
            "events and the async-ingest webhook. A uuid4 hex is generated "
            "when absent."
        ),
    },
    "IdempotencyKey": {
        "name": "Idempotency-Key",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "maxLength": 255},
        "description": (
            "Mandatory on every agent-tier POST / PUT / DELETE "
            "(``400 missing_idempotency_key`` otherwise). A replay with the "
            "same key within the store TTL returns the original response "
            "without re-dispatching; keys are namespaced per "
            "``(tenant, route scope)``."
        ),
    },
}


def _security_for(method: str, path: str) -> list[dict[str, list[str]]]:
    """Security requirement alternatives for one operation.

    * public operations: none;
    * agent tier: the agent token, or either platform-key form;
    * everything else: either platform-key form.

    Each entry is one alternative (OpenAPI ``security`` is an OR list),
    which is exactly how :class:`ApiKeyMiddleware` treats them.
    """
    if (method, path) in PUBLIC_OPERATIONS:
        return []
    platform = [{"ApiKeyHeader": []}, {"ApiKeyAuthorization": []}]
    if path.startswith(AGENT_PREFIX):
        return [{"AgentToken": []}, *platform]
    return platform


def _header_parameters_for(method: str, path: str) -> list[dict[str, str]]:
    """``$ref`` list of the header parameters one operation reads."""
    if (method, path) in PUBLIC_OPERATIONS:
        return []
    refs = ["XTenantId", "XWorkspaceId", "XCorrelationId"]
    if path.startswith(AGENT_PREFIX) and method in _MUTATING:
        refs.append("IdempotencyKey")
    return [{"$ref": f"#/components/parameters/{name}"} for name in refs]


def apply_wire_contract(spec: dict[str, Any]) -> dict[str, Any]:
    """Add ``securitySchemes``, reusable header parameters and per-operation refs.

    Idempotent: a header already listed on an operation (by name) is
    not added twice, so re-running over a cached spec is safe.
    """
    components = spec.setdefault("components", {})
    components.setdefault("securitySchemes", {}).update(SECURITY_SCHEMES)
    components.setdefault("parameters", {}).update(HEADER_PARAMETERS)
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict) or method.lower() not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "head",
                "options",
            }:
                continue
            verb = method.lower()
            security = _security_for(verb, path)
            if security:
                operation["security"] = security
            params: list[dict[str, Any]] = operation.setdefault("parameters", [])
            present = {p.get("$ref") for p in params if isinstance(p, dict)}
            for ref in _header_parameters_for(verb, path):
                if ref["$ref"] not in present:
                    params.append(ref)
            if not params:
                operation.pop("parameters", None)
    return spec


def install_openapi(
    app: FastAPI,
    context: ApplicationContext,
    *,
    title: str,
    version: str,
    description: str,
) -> None:
    """Replace ``app.openapi`` with a pyfly-driven generator.

    Cached after the first call -- FastAPI's own ``openapi()`` method
    caches via ``app.openapi_schema`` and our override follows the
    same contract.
    """
    registrar = ControllerRegistrar()
    generator = OpenAPIGenerator(title=title, version=version, description=description)

    def _custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        route_metadata = registrar.collect_route_metadata(context)
        spec = generator.generate(route_metadata=route_metadata)

        # Enrich tag entries with human-readable descriptions.
        if spec.get("tags"):
            for tag in spec["tags"]:
                name = tag.get("name")
                if name and name in TAG_DESCRIPTIONS:
                    tag["description"] = TAG_DESCRIPTIONS[name]

        # Add the deployment's contact info + servers + license to
        # the info block so the docs landing page is usable on its
        # own (without a separate page).
        info = spec.setdefault("info", {})
        info.setdefault(
            "contact",
            {
                "name": "Firefly OperationOS",
                "url": "https://github.com/firefly-operationOS/flycanon",
            },
        )
        info.setdefault(
            "license",
            {
                "name": "Apache-2.0",
                "url": "https://github.com/firefly-operationOS/flycanon/blob/main/LICENSE",
            },
        )

        # Servers block: lets Swagger UI's "Try it out" send to the
        # right host without manual editing.
        spec.setdefault(
            "servers",
            [
                {"url": "/", "description": "This service"},
            ],
        )

        # Headers + credentials the introspector cannot see (they are
        # read off ``Request.headers`` inside the controllers).
        apply_wire_contract(spec)

        app.openapi_schema = spec
        logger.info(
            "openapi schema generated (paths=%d, schemas=%d, tags=%d)",
            len(spec.get("paths", {})),
            len((spec.get("components") or {}).get("schemas", {})),
            len(spec.get("tags", [])),
        )
        return spec

    app.openapi = _custom_openapi  # type: ignore[method-assign]
