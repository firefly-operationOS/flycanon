# Copyright 2026 Firefly Software Solutions Inc
"""flycanon REST controllers.

Each controller is a ``@rest_controller``-decorated class that wraps a
single CQRS bus call. Controllers carry no business logic.
"""

from __future__ import annotations
