# Copyright 2026 Firefly Software Solutions Inc
"""Search + RAG-answer DTOs.

Two retrieval surfaces:

* ``POST /api/v1/search`` -- raw hybrid retrieval (BM25 + vector +
  RRF fusion). Returns a hit list ordered by fused score.
* ``POST /api/v1/query``  -- runs an answerer over the top hits and
  returns a grounded answer with citations.

Both paths share the same filter model so callers can scope by
domain, jurisdiction, source id, or arbitrary tag.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


class _Filters(BaseModel):
    """Shared retrieval filters."""

    source_ids: list[str] | None = Field(
        default=None,
        description="Restrict retrieval to chunks of these sources.",
    )
    knowledge_item_ids: list[str] | None = Field(
        default=None,
        description="Restrict retrieval to chunks cited by these knowledge items.",
    )
    domains: list[Domain] | None = Field(default=None)
    jurisdictions: list[Jurisdiction] | None = Field(default=None)
    tags: list[str] | None = Field(default=None)
    statuses: list[KnowledgeStatus] | None = Field(
        default=None,
        description="Restrict to chunks belonging to knowledge items in these statuses.",
    )


class SearchRequest(_Filters):
    """Hybrid-retrieval request."""

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=200)
    per_query_k: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description="Override the per-stage k. Defaults to ``settings.retrieval_per_query_k``.",
    )


class Hit(BaseModel):
    """Single retrieval hit."""

    chunk_id: str
    source_id: str
    knowledge_item_id: str | None = Field(default=None)
    knowledge_version: int | None = Field(default=None)
    content: str
    score: float = Field(description="Fused RRF score.")
    bm25_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    hits: list[Hit]
    elapsed_ms: int = Field(ge=0)


class AnswerRequest(_Filters):
    """RAG-answer request."""

    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=40, description="Hits to ground the answer with.")
    instructions: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional system-level steering appended to the answer prompt.",
    )
    model: str | None = Field(
        default=None,
        description="Override the configured answer model (provider:model string).",
    )


class AnswerResponse(BaseModel):
    answer: str
    citations: list[Hit]
    model: str
    elapsed_ms: int = Field(ge=0)
    no_answer: bool = Field(
        default=False,
        description="True when no chunks crossed the relevance floor.",
    )
