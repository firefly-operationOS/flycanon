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

"""SQLAlchemy ORM entities.

Every persistent type in flycanon lives here. The shared declarative
:class:`Base` is imported by Alembic's ``env.py`` to populate
``target_metadata``; ``alembic upgrade head`` discovers tables by
metadata, not by walking modules.
"""

from __future__ import annotations

from flycanon.models.entities.agent_token import AgentToken
from flycanon.models.entities.audit_event import AuditEventRow
from flycanon.models.entities.base import Base
from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.conversation import ConversationRow, ConversationTurnRow
from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.entities.ingest_job import IngestJobEventRow, IngestJobRow
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow
from flycanon.models.entities.taxonomy_node import TaxonomyNodeRow
from flycanon.models.entities.workspace import Workspace

__all__ = [
    "AgentToken",
    "AuditEventRow",
    "Base",
    "CandidateRow",
    "CitationRow",
    "ConversationRow",
    "ConversationTurnRow",
    "CostEventRow",
    "IngestJobEventRow",
    "IngestJobRow",
    "KnowledgeChunkRow",
    "KnowledgeItemRow",
    "KnowledgeRelationRow",
    "KnowledgeVersionRow",
    "SourceRow",
    "TaxonomyNodeRow",
    "Workspace",
]
