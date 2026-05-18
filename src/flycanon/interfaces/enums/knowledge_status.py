# Copyright 2026 Firefly Software Solutions Inc
"""``KnowledgeStatus`` -- canonical-item lifecycle.

Each knowledge item points at the current version; every transition is
recorded as a new version row, never an in-place mutation. The status
on the *item* mirrors the status on the *current version*.

* ``draft``      -- the version exists but is not yet validated; no
                    downstream consumer should treat it as authoritative.
* ``published``  -- the current version is canonical. Retrievers return
                    chunks from published items by default.
* ``superseded`` -- a newer version is canonical; this version is
                    retained for historical traceability but never
                    surfaced as a hit.
* ``retired``    -- the entire item (every version) is withdrawn; no
                    longer indexed and no longer cited.
"""

from __future__ import annotations

from enum import StrEnum


class KnowledgeStatus(StrEnum):
    draft = "draft"
    published = "published"
    superseded = "superseded"
    retired = "retired"
