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

"""flycanon persistence layer.

Owns the ORM entities (``flycanon.models.entities``) and the thin
async repositories that wrap them (``flycanon.models.repositories``).
Nothing in this package should depend on ``flycanon.core`` or
``flycanon.web``; the dependency arrow points the other way.
"""

from __future__ import annotations

from flycanon.models.repositories import (
    AuditRepository,
    CandidateRepository,
    ChunkRepository,
    KnowledgeRepository,
    SourceRepository,
    TaxonomyRepository,
)

__all__ = [
    "AuditRepository",
    "CandidateRepository",
    "ChunkRepository",
    "KnowledgeRepository",
    "SourceRepository",
    "TaxonomyRepository",
]
