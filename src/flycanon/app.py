# Copyright 2026 Firefly Software Solutions Inc
"""PyFly application entry point for flycanon.

``scan_packages`` declares every package containing ``@configuration``,
``@rest_controller``, ``@service``, ``@command_handler``,
``@query_handler``, or ``@repository`` beans so pyfly's DI container
can discover them at boot.

Exception handlers are registered explicitly via
``flycanon.web.conventions.register_exception_handlers(app)`` in
``flycanon.main`` -- pyfly's FastAPI adapter does not scan
``@controller_advice`` beans, so the conventions handler table is
hand-wired against the FastAPI app.
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
        "flycanon.web.controllers",  # REST controllers (user-tier)
        "flycanon.web.controllers.agent",  # REST controllers (agent-tier)
    ],
)
class CanonApplication:
    """Marker class consumed by :class:`PyFlyApplication` at boot."""
