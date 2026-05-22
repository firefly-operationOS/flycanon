# Copyright 2026 Firefly Software Solutions Inc
"""``WorkspaceStatus`` -- workspace lifecycle status.

Mirrors the values written to the ``canon_workspaces.status`` column
and exposed on the ``WorkspaceSpec`` DTO. The values are part of the
public API contract -- renaming a member is a breaking change.

* ``draft``      -- the workspace exists but is not yet active; no
                    knowledge ingestion or retrieval should target it.
* ``active``     -- the default working state; reads and writes are
                    permitted.
* ``closed``     -- the workspace is no longer accepting writes;
                    reads remain available for audit and traceability.
* ``handed_off`` -- ownership transferred to another tenant or
                    workspace; the row is preserved for provenance but
                    is otherwise inert.
"""

from __future__ import annotations

from enum import StrEnum


class WorkspaceStatus(StrEnum):
    draft = "draft"
    active = "active"
    closed = "closed"
    handed_off = "handed_off"
