# Copyright 2026 Firefly Software Solutions Inc
"""flycanon -- Operational Knowledge Repository service.

The single public symbol is :data:`__version__`. Importing the
top-level package does not boot the application; that is the job of
:mod:`flycanon.main` (ASGI) or :mod:`flycanon.cli` (CLI).
"""

from __future__ import annotations

__version__ = "26.5.4"

__all__ = ["__version__"]
