# Copyright 2026 Firefly Software Solutions Inc
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
