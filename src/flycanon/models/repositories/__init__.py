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

"""Async repositories for every flycanon entity.

Each repository owns one table. They share the same constructor /
``from_url`` factory pattern so the DI container can instantiate them
uniformly and the actuator's database health probe can reach the
underlying async engine through the ``engine`` property.
"""

from __future__ import annotations

from flycanon.models.repositories.agent_token_repository import AgentTokenRepository
from flycanon.models.repositories.audit_repository import AuditRepository
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.conversation_repository import ConversationRepository
from flycanon.models.repositories.cost_repository import CostRepository
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.relation_repository import RelationRepository
from flycanon.models.repositories.source_repository import SourceRepository
from flycanon.models.repositories.taxonomy_repository import TaxonomyRepository
from flycanon.models.repositories.workspace_repository import WorkspaceRepository

__all__ = [
    "AgentTokenRepository",
    "AuditRepository",
    "CandidateRepository",
    "ChunkRepository",
    "ConversationRepository",
    "CostRepository",
    "IngestJobRepository",
    "KnowledgeRepository",
    "RelationRepository",
    "SourceRepository",
    "TaxonomyRepository",
    "WorkspaceRepository",
]
