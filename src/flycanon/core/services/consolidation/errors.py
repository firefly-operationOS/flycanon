# Copyright 2026 Firefly Software Solutions Inc
"""Typed errors raised by the consolidation pipeline."""

from __future__ import annotations


class ConsolidationError(Exception):
    code: str = "consolidation_failed"
    http_status: int = 422


class CandidateNotFound(ConsolidationError):
    code = "candidate_not_found"
    http_status = 404

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"candidate {candidate_id!r} not found")
        self.candidate_id = candidate_id


class CandidateAlreadyDecided(ConsolidationError):
    code = "candidate_already_decided"
    http_status = 409

    def __init__(self, candidate_id: str, status: str) -> None:
        super().__init__(f"candidate {candidate_id!r} is already {status!r}; cannot re-decide")
        self.candidate_id = candidate_id
        self.status = status
