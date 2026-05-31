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
