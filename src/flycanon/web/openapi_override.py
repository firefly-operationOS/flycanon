# Copyright 2026 Firefly Software Solutions Inc
"""Replace FastAPI's auto-generated OpenAPI with pyfly's richer schema.

The pyfly FastAPI adapter registers every controller method behind a
single ``lazy_endpoint(request: Request)`` shim so the DI container can
resolve the controller bean on first hit. The side-effect is that
FastAPI's built-in OpenAPI introspector sees only that shim -- no
request body, no response model, no tags, no docstring.

This module bridges the gap. After the FastAPI app is built we install
a custom ``app.openapi`` callable that:

1. Collects per-route metadata from the original controller signatures
   via pyfly's :class:`ControllerRegistrar.collect_route_metadata`,
2. Renders the spec through pyfly's :class:`OpenAPIGenerator`,
3. Enriches the result with global tags (with descriptions) and the
   OpenAPI ``info`` block we want Swagger / ReDoc to display.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from pyfly.context.application_context import ApplicationContext
from pyfly.web.adapters.starlette.controller import ControllerRegistrar
from pyfly.web.openapi import OpenAPIGenerator

logger = logging.getLogger(__name__)


#: Per-tag descriptions shown on the Swagger landing page. Controllers
#: register their tag via the ``@rest_controller(tags=[...])`` argument;
#: the descriptions below render alongside on the docs landing page.
TAG_DESCRIPTIONS: dict[str, str] = {
    "Sources": (
        "Source intake -- DOCX, PDF, HTML, Markdown, or plain-text "
        "files become canonical sources. The pipeline hashes the bytes "
        "(idempotency), parses + chunks the content, embeds every "
        "chunk, and indexes both the BM25 (FTS5) and vector projections."
    ),
    "Knowledge": (
        "Canonical knowledge items -- the validated, versioned units "
        "downstream consumers should treat as ground truth. Every "
        "publish, supersession, and retirement is captured in the "
        "knowledge-version history and broadcast on ``flycanon.knowledge``."
    ),
    "Candidates": (
        "Pre-canonical knowledge proposals derived from sources by the "
        "consolidation stage. Accept a candidate to materialise it as "
        "a new knowledge version; reject one to mark it discarded."
    ),
    "Query": (
        "Hybrid search and retrieval-augmented answering. ``/search`` "
        "returns the raw fused hit list (BM25 + vector + RRF). "
        "``/query`` runs the answerer over the top hits and returns "
        "a grounded answer with citations."
    ),
    "Taxonomy": (
        "Domain + jurisdiction taxonomy that scopes every knowledge "
        "item and every retrieval. The default tree mirrors the "
        "workshop personas (Legal, Compliance, Process, Network, AI "
        "Platform, Executive, HR, CTO, Engineering, Security)."
    ),
    "Audit": (
        "Append-only audit log -- every mutation is captured with "
        "actor, payload, and trace-context for compliance projections."
    ),
    "Version": (
        "Service identity, primary / fallback model, and EDA adapter "
        "-- useful for smoke tests and operations dashboards."
    ),
}


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

        # Surface the deployment's primary endpoints in the info block.
        info = spec.setdefault("info", {})
        info.setdefault(
            "contact",
            {"name": "Firefly OperationOS", "url": "https://github.com/firefly-operationOS"},
        )

        app.openapi_schema = spec
        logger.info(
            "openapi schema generated (paths=%d, schemas=%d, tags=%d)",
            len(spec.get("paths", {})),
            len((spec.get("components") or {}).get("schemas", {})),
            len(spec.get("tags", [])),
        )
        return spec

    app.openapi = _custom_openapi  # type: ignore[method-assign]
