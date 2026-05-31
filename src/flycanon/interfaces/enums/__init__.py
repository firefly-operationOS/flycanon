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

"""Public enums shipped on the wire.

The values are part of the API contract -- renaming a member is a
breaking change.
"""

from __future__ import annotations

from flycanon.interfaces.enums.candidate_status import CandidateStatus
from flycanon.interfaces.enums.domain import Domain
from flycanon.interfaces.enums.jurisdiction import Jurisdiction
from flycanon.interfaces.enums.knowledge_status import KnowledgeStatus
from flycanon.interfaces.enums.relation_kind import RelationKind
from flycanon.interfaces.enums.source_kind import SourceKind
from flycanon.interfaces.enums.source_status import SourceStatus
from flycanon.interfaces.enums.workspace_status import WorkspaceStatus

__all__ = [
    "CandidateStatus",
    "Domain",
    "Jurisdiction",
    "KnowledgeStatus",
    "RelationKind",
    "SourceKind",
    "SourceStatus",
    "WorkspaceStatus",
]
