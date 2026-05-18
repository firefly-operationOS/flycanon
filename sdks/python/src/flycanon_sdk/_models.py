# Copyright 2026 Firefly Software Solutions Inc
"""SDK-local Pydantic models that mirror the service's public DTOs.

We re-declare the models here (instead of importing them from the
service package) so the SDK can be installed in any consumer
codebase without the framework dependency tree.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetails(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str | None = None
    extensions: dict[str, Any] | None = None


class SourceMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    title: str | None = None
    author: str | None = None
    domain: str | None = None
    jurisdiction: str | None = None
    language: str | None = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class SubmitSourceJsonPayload(BaseModel):
    kind: str = "unknown"
    uri: str | None = None
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    content_base64: str | None = None
    filename: str | None = None
    content_type: str | None = None


class SourceRecord(BaseModel):
    id: str
    kind: str
    status: str
    filename: str | None = None
    uri: str | None = None
    content_sha256: str
    content_bytes: int
    n_chunks: int = 0
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    ingested_at: datetime | None = None
    updated_at: datetime


class SourcesPage(BaseModel):
    items: list[SourceRecord]
    total: int
    offset: int
    limit: int


class Citation(BaseModel):
    source_id: str
    chunk_id: str | None = None
    quote: str | None = None
    relevance: float | None = None
    page: int | None = None


class KnowledgeItem(BaseModel):
    id: str
    status: str
    current_version: int
    title: str
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None = None
    summary: str | None = None


class KnowledgeVersion(BaseModel):
    knowledge_item_id: str
    version: int
    status: str
    title: str
    summary: str | None = None
    body: str
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    supersedes_version: int | None = None
    superseded_by_version: int | None = None
    created_by: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeItemsPage(BaseModel):
    items: list[KnowledgeItem]
    total: int
    offset: int
    limit: int


class Provenance(BaseModel):
    knowledge_item_id: str
    version: int
    citations: list[Citation]
    sources: list[dict[str, Any]] = Field(default_factory=list)
    history: list[KnowledgeVersion] = Field(default_factory=list)


class CreateKnowledgeRequest(BaseModel):
    title: str
    body: str
    summary: str | None = None
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    publish: bool = True
    actor: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    summary: str | None = None
    domain: str | None = None
    jurisdiction: str | None = None
    tags: list[str] | None = None
    citations: list[Citation] | None = None
    publish: bool = True
    actor: str | None = None
    metadata: dict[str, Any] | None = None


class SupersedeKnowledgeRequest(BaseModel):
    superseded_by_item_id: str
    reason: str | None = None
    actor: str | None = None


class RetireKnowledgeRequest(BaseModel):
    reason: str
    actor: str | None = None


class CandidateRecord(BaseModel):
    id: str
    status: str
    source_id: str
    title: str
    body: str
    summary: str | None = None
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    score: float | None = None
    rationale: str | None = None
    materialised_knowledge_item_id: str | None = None
    materialised_version: int | None = None
    actor: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidatesPage(BaseModel):
    items: list[CandidateRecord]
    total: int
    offset: int
    limit: int


class ProposeCandidateRequest(BaseModel):
    source_id: str
    domain: str | None = None
    jurisdiction: str | None = None
    max_chunks: int = 40
    instructions: str | None = None
    actor: str | None = None


class AcceptCandidateRequest(BaseModel):
    target_item_id: str | None = None
    publish: bool = True
    actor: str | None = None
    note: str | None = None


class RejectCandidateRequest(BaseModel):
    reason: str
    actor: str | None = None


class TaxonomyNode(BaseModel):
    id: str
    parent_id: str | None
    slug: str
    label: str
    domain: str
    description: str | None = None
    depth: int
    created_at: datetime
    updated_at: datetime


class TaxonomyTree(BaseModel):
    nodes: list[TaxonomyNode]


class CreateTaxonomyNodeRequest(BaseModel):
    parent_id: str | None = None
    slug: str
    label: str
    domain: str
    description: str | None = None


class Hit(BaseModel):
    chunk_id: str
    source_id: str
    knowledge_item_id: str | None = None
    knowledge_version: int | None = None
    content: str
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    per_query_k: int | None = None
    source_ids: list[str] | None = None
    knowledge_item_ids: list[str] | None = None
    domains: list[str] | None = None
    jurisdictions: list[str] | None = None
    tags: list[str] | None = None
    statuses: list[str] | None = None


class SearchResponse(BaseModel):
    hits: list[Hit]
    elapsed_ms: int


class AnswerRequest(BaseModel):
    question: str
    top_k: int = 8
    instructions: str | None = None
    model: str | None = None
    source_ids: list[str] | None = None
    knowledge_item_ids: list[str] | None = None
    domains: list[str] | None = None
    jurisdictions: list[str] | None = None
    tags: list[str] | None = None
    statuses: list[str] | None = None


class AnswerResponse(BaseModel):
    answer: str
    citations: list[Hit]
    model: str
    elapsed_ms: int
    no_answer: bool = False


class AuditEvent(BaseModel):
    id: str
    occurred_at: datetime
    event_type: str
    actor: str | None = None
    subject_id: str
    subject_kind: str
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    items: list[AuditEvent]
    total: int
    offset: int
    limit: int


class VersionInfo(BaseModel):
    service: str
    version: str
    embedding_model: str
    answer_model: str
    answer_fallback_model: str
    vector_store: str
    eda_adapter: str
