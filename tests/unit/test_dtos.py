# Copyright 2026 Firefly Software Solutions Inc
"""Public DTO validation -- minimal but cross-module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flycanon.interfaces.dtos import (
    AnswerRequest,
    CreateKnowledgeRequest,
    ProblemDetails,
    SearchRequest,
)
from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


def test_problem_details_round_trip():
    pd = ProblemDetails(
        type="https://flycanon.dev/problems/source-not-found",
        title="Source not found",
        status=404,
        code="source_not_found",
        detail="source 'abc' not found",
        extensions={"source_id": "abc"},
    )
    dumped = pd.model_dump(exclude_none=True)
    again = ProblemDetails.model_validate(dumped)
    assert again.code == "source_not_found"
    assert again.extensions == {"source_id": "abc"}


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
    req = CreateKnowledgeRequest(
        title="x", body="y", domain=Domain.process
    )
    assert req.jurisdiction == Jurisdiction.GLOBAL
