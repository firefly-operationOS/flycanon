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

"""Entity <-> public-DTO mappers.

Every public response shape is built by a function here; no
controller should hand a SQLAlchemy row to FastAPI directly. The
mappers also convert the JSON columns (``tags_json``, ``citations_json``)
into typed lists / DTOs so the wire shape is self-describing.
"""

from __future__ import annotations

from flycanon.core.mappers.audit_mapper import to_audit_event
from flycanon.core.mappers.candidate_mapper import to_candidate_record
from flycanon.core.mappers.job_mapper import to_ingest_job
from flycanon.core.mappers.knowledge_mapper import (
    to_citation,
    to_knowledge_item,
    to_knowledge_version,
)
from flycanon.core.mappers.relation_mapper import to_knowledge_relation
from flycanon.core.mappers.source_mapper import to_source_record
from flycanon.core.mappers.taxonomy_mapper import to_taxonomy_node

__all__ = [
    "to_audit_event",
    "to_candidate_record",
    "to_citation",
    "to_ingest_job",
    "to_knowledge_item",
    "to_knowledge_relation",
    "to_knowledge_version",
    "to_source_record",
    "to_taxonomy_node",
]
