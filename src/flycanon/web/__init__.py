# Copyright 2026 Firefly Software Solutions Inc
"""flycanon web tier -- REST controllers + global exception advice.

The web tier is the only thing that speaks
``application/json`` over HTTP. It depends on
:class:`pyfly.cqrs.CommandBus` / :class:`pyfly.cqrs.QueryBus` and the
DTOs in :mod:`flycanon.interfaces.dtos`; it must not import from
``flycanon.core.services.*`` directly.
"""

from __future__ import annotations
