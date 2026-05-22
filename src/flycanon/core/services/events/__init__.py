# Copyright 2026 Firefly Software Solutions Inc
"""Domain-event publishers.

Each publisher in this package wraps the pyfly-provided
:class:`pyfly.eda.EventPublisher` and translates a domain entity into
the canonical wire envelope for one event family. Centralising the
translation logic keeps controllers and command handlers thin: they
hand the publisher a domain object and forget about topic names,
payload shapes, and EDA failure handling.
"""

from __future__ import annotations

from flycanon.core.services.events.workspace_event_publisher import WorkspaceEventPublisher

__all__ = ["WorkspaceEventPublisher"]
