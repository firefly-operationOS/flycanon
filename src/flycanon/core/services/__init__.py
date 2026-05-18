# Copyright 2026 Firefly Software Solutions Inc
"""flycanon core services.

Each subpackage owns a single concern (ingestion, embeddings,
retrieval, knowledge, query, ...) and exposes its public types through
its ``__init__.py``. Controllers and CQRS handlers depend on these
services via constructor injection -- never on each other.
"""

from __future__ import annotations
