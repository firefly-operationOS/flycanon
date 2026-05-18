# Copyright 2026 Firefly Software Solutions Inc
"""PyFly application entry point for flycanon.

``scan_packages`` declares every package containing ``@configuration``,
``@rest_controller``, ``@controller_advice``, ``@service``,
``@command_handler``, ``@query_handler``, or ``@repository`` beans so
pyfly's DI container can discover them at boot.
"""

from __future__ import annotations

from pyfly.core import pyfly_application
from pyfly.starters.core import enable_core_stack


@enable_core_stack
@pyfly_application(
    name="flycanon",
    version="26.5.4",
    description=(
        "flycanon -- Operational Knowledge Repository. Versioned, "
        "provenance-tracked canonical knowledge with hybrid retrieval "
        "and RAG, exposed as a standalone HTTP microservice. Part of "
        "Firefly OperationOS, platform-agnostic by design."
    ),
    scan_packages=[
        "flycanon.core",  # @configuration class
        "flycanon.core.services",  # CQRS handlers + @service beans
        "flycanon.web.controllers",  # REST controllers
        "flycanon.web.advice",  # exception advice
    ],
)
class CanonApplication:
    """Marker class consumed by :class:`PyFlyApplication` at boot."""
