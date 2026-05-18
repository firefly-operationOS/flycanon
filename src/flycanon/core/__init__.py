# Copyright 2026 Firefly Software Solutions Inc
"""flycanon core layer -- DI configuration + services.

This package is the only place where pyfly bean wiring outside the
``@service`` / ``@command_handler`` / ``@query_handler`` stereotype
decorators is declared. Controllers must never import from
``flycanon.core.services.*`` directly -- they take the
:class:`pyfly.cqrs.CommandBus` / :class:`pyfly.cqrs.QueryBus` and the
DTOs the bus consumes.
"""

from __future__ import annotations
