# Copyright 2026 Firefly Software Solutions Inc
"""Corpus / queue / cost inventory DTOs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SourceStats(BaseModel):
    """Counts + total bytes for ``canon_sources``."""

    total: int = Field(ge=0)
    by_kind: dict[str, int] = Field(
        default_factory=dict,
        description="Counts per ``SourceRow.kind`` (one of the routing-matrix labels).",
    )
    by_status: dict[str, int] = Field(
        default_factory=dict,
        description="Counts per ``SourceRow.status`` (ingested / failed / superseded / …).",
    )
    total_bytes: int = Field(
        ge=0,
        description="Sum of ``content_bytes`` across all source rows.",
    )


class KnowledgeItemStats(BaseModel):
    """Counts of canonical knowledge items by status + domain."""

    total: int = Field(ge=0)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_domain: dict[str, int] = Field(default_factory=dict)


class CandidateStats(BaseModel):
    """Pre-canonical LLM proposals by status."""

    total: int = Field(ge=0)
    by_status: dict[str, int] = Field(default_factory=dict)


class ChunkStats(BaseModel):
    """Coverage of the retrieval index."""

    total: int = Field(ge=0)
    embedded: int = Field(ge=0)
    embedded_pct: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of chunks with a non-NULL embedding vector.",
    )


class JobStats(BaseModel):
    """Async-ingest queue health."""

    total: int = Field(ge=0)
    by_status: dict[str, int] = Field(default_factory=dict)
    avg_attempts: float = Field(ge=0.0)


class CostStreamStats(BaseModel):
    """Quick spend headline for the inventory panel.

    Detailed breakdowns live behind ``/api/v1/billing/*``; this is the
    rollup a dashboard can show alongside the corpus counters.
    """

    total_events: int = Field(ge=0)
    cost_usd_24h: str = Field(description="Decimal USD spent in the last 24 hours.")
    cost_usd_30d: str = Field(description="Decimal USD spent in the last 30 days.")


class CorpusStats(BaseModel):
    """``GET /api/v1/stats`` envelope -- one round-trip for ops dashboards."""

    generated_at: datetime
    sources: SourceStats
    knowledge_items: KnowledgeItemStats
    knowledge_versions: int = Field(ge=0, description="Total rows across ``canon_knowledge_versions``.")
    candidates: CandidateStats
    chunks: ChunkStats
    ingest_jobs: JobStats
    cost: CostStreamStats
