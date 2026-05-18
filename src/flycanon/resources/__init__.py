# Copyright 2026 Firefly Software Solutions Inc
"""Static resources packaged with the flycanon wheel.

Prompts, seed taxonomies, and any other read-only artefact the
runtime needs. Loaders should reach for files under this package via
``importlib.resources`` so the assets are reachable from inside the
distroless container.
"""

from __future__ import annotations
