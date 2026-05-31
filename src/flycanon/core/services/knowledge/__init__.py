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

"""Knowledge-item lifecycle service.

Owns the state machine for canonical knowledge -- create, update,
supersede, retire -- and emits the corresponding events on the
``flycanon.knowledge`` topic. Every transition is mirrored in the
audit log through :class:`AuditService`.
"""

from __future__ import annotations

from flycanon.core.services.knowledge.diff_service import KnowledgeDiffService
from flycanon.core.services.knowledge.errors import (
    KnowledgeItemAlreadyRetired,
    KnowledgeItemNotFound,
    KnowledgeServiceError,
)
from flycanon.core.services.knowledge.graph_service import KnowledgeGraphService
from flycanon.core.services.knowledge.knowledge_service import KnowledgeService
from flycanon.core.services.knowledge.provenance_service import ProvenanceService
from flycanon.core.services.knowledge.relation_service import (
    InvalidRelationError,
    KnowledgeRelationService,
    RelationConflictError,
    RelationNotFoundError,
)

__all__ = [
    "InvalidRelationError",
    "KnowledgeDiffService",
    "KnowledgeGraphService",
    "KnowledgeItemAlreadyRetired",
    "KnowledgeItemNotFound",
    "KnowledgeRelationService",
    "KnowledgeService",
    "KnowledgeServiceError",
    "ProvenanceService",
    "RelationConflictError",
    "RelationNotFoundError",
]
