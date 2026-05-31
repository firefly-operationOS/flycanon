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

"""Public DTO validation -- minimal but cross-module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flycanon.interfaces.dtos import (
    AnswerRequest,
    CreateKnowledgeRequest,
    SearchRequest,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


def test_create_knowledge_request_rejects_empty_title():
    with pytest.raises(ValidationError):
        CreateKnowledgeRequest(title="", body="x", domain=Domain.process)


def test_search_request_clamps_top_k():
    SearchRequest(query="q", top_k=200)
    with pytest.raises(ValidationError):
        SearchRequest(query="q", top_k=500)
    with pytest.raises(ValidationError):
        SearchRequest(query="q", top_k=0)


def test_answer_request_accepts_status_filter_enum():
    req = AnswerRequest(
        question="why?",
        top_k=4,
        statuses=[KnowledgeStatus.published, KnowledgeStatus.draft],
    )
    assert req.statuses[0] == KnowledgeStatus.published


def test_create_knowledge_request_defaults_jurisdiction_to_global():
    req = CreateKnowledgeRequest(title="x", body="y", domain=Domain.process)
    assert req.jurisdiction == Jurisdiction.GLOBAL
