# Copyright 2026 Firefly Software Solutions Inc
"""``CandidateStatus`` -- pre-canonical proposal lifecycle.

Candidates are emitted by the consolidation stage; humans (or an
automated policy) accept, reject, or merge them into existing
knowledge items.

* ``proposed`` -- emitted by the consolidator, awaiting decision.
* ``accepted`` -- materialised as a new knowledge version. The
                  ``materialised_knowledge_item_id`` field on the
                  Candidate DTO points to the resulting item.
* ``rejected`` -- discarded with a free-form reason.
* ``merged``   -- folded into an existing knowledge item as an
                  additional citation or evidence row, without
                  creating a new version.
"""

from __future__ import annotations

from enum import StrEnum


class CandidateStatus(StrEnum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    merged = "merged"
