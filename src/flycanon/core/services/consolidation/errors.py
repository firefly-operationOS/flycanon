# Copyright 2026 Firefly Software Solutions Inc
"""Typed errors raised by the consolidation pipeline.

Each subclass inherits from :class:`FireflyHTTPException` so the
conventions exception handler renders it as an RFC 7807
``ProblemDetail`` automatically.
"""

from __future__ import annotations

from typing import ClassVar

from flycanon.web.conventions import FireflyHTTPException


class ConsolidationError(FireflyHTTPException):
    status: ClassVar[int] = 422
    code: ClassVar[str] = "consolidation_failed"
    title: ClassVar[str] = "Consolidation failed"


class CandidateNotFound(ConsolidationError):
    status = 404
    code = "candidate_not_found"
    title = "Candidate not found"

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"candidate {candidate_id!r} not found")
        self.candidate_id = candidate_id


class CandidateAlreadyDecided(ConsolidationError):
    status = 409
    code = "candidate_already_decided"
    title = "Candidate already decided"

    def __init__(self, candidate_id: str, candidate_status: str) -> None:
        super().__init__(f"candidate {candidate_id!r} is already {candidate_status!r}; cannot re-decide")
        self.candidate_id = candidate_id
        # ``status`` on the base class is the HTTP status ClassVar;
        # the per-instance candidate-state lives on ``candidate_status``.
        self.candidate_status = candidate_status
