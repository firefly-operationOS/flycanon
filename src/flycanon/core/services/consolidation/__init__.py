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
