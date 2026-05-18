# Copyright 2026 Firefly Software Solutions Inc
"""Candidate DTOs -- pre-canonical knowledge proposals.

The consolidation stage reads source chunks, asks an LLM to synthesise
canonical statements, and lands each one as a :class:`CandidateRecord`
in ``proposed`` status. Humans (or an automated policy) accept,
reject, or merge them. Accepted candidates materialise as a new
knowledge version with the candidate's citations attached.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from flycanon.interfaces.dtos.knowledge import Citation
from flycanon.interfaces.enums import CandidateStatus, Domain, Jurisdiction


class ProposeCandidateRequest(BaseModel):
    """Trigger consolidation against a source (synchronous).

    The handler reads up to ``max_chunks`` chunks from the source,
    runs the consolidation prompt, and persists the resulting
    candidates. Callers receive the list of newly proposed records;
    no candidate is auto-accepted.
    """

    source_id: str
    domain: Domain | None = Field(default=None, description="Restrict the proposal to this domain.")
    jurisdiction: Jurisdiction | None = Field(default=None)
    max_chunks: int = Field(default=40, ge=1, le=500)
    instructions: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional caller instructions appended to the consolidation prompt.",
    )
    actor: str | None = Field(default=None)


class CandidateRecord(BaseModel):
    """Public view of a candidate row."""

    id: str
    status: CandidateStatus
    source_id: str
    title: str
    body: str
    summary: str | None = Field(default=None)
    domain: Domain
    jurisdiction: Jurisdiction = Field(default=Jurisdiction.GLOBAL)
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Consolidator's self-reported confidence in this proposal.",
    )
    rationale: str | None = Field(default=None, max_length=4000)
    materialised_knowledge_item_id: str | None = Field(default=None)
    materialised_version: int | None = Field(default=None)
    actor: str | None = Field(default=None)
    created_at: datetime
    decided_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidatesPage(BaseModel):
    items: list[CandidateRecord]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)


class AcceptCandidateRequest(BaseModel):
    """Materialise the candidate as a new knowledge version.

    ``target_item_id`` is optional. If set, the candidate is appended
    as a new version on the existing item; if omitted, a new
    knowledge item is created.
    """

    target_item_id: str | None = Field(default=None)
    publish: bool = Field(default=True)
    actor: str | None = Field(default=None)
    note: str | None = Field(default=None, max_length=2000)


class RejectCandidateRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    actor: str | None = Field(default=None)
