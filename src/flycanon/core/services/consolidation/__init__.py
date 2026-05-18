# Copyright 2026 Firefly Software Solutions Inc
"""LLM-driven consolidation -- source chunks -> candidate proposals.

The consolidator reads a slice of a source's chunks and asks the
configured LLM to synthesise canonical knowledge statements. Each
statement comes back with citations into the chunk set, a self-rated
confidence, and a free-form rationale. The :class:`CandidateService`
persists every proposal and exposes the accept / reject lifecycle
the controllers drive.
"""

from __future__ import annotations

from flycanon.core.services.consolidation.candidate_service import CandidateService
from flycanon.core.services.consolidation.consolidator import (
    CandidateProposal,
    ConsolidationOutput,
    Consolidator,
)
from flycanon.core.services.consolidation.errors import (
    CandidateAlreadyDecided,
    CandidateNotFound,
    ConsolidationError,
)

__all__ = [
    "CandidateAlreadyDecided",
    "CandidateNotFound",
    "CandidateProposal",
    "CandidateService",
    "ConsolidationError",
    "ConsolidationOutput",
    "Consolidator",
]
