# Copyright 2026 Firefly Software Solutions Inc
"""``VersionInfo`` -- identity payload served by ``GET /api/v1/version``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VersionInfo(BaseModel):
    """Identity of the running service instance."""

    service: str = Field(description="Service slug.", examples=["flycanon"])
    version: str = Field(
        description=(
            "CalVer (``YY.MM.PP``) baked into the wheel at build time. "
            "PEP 440 normalises ``26.05.01`` -> ``26.5.1`` so the value "
            "you see here uses the stripped form."
        ),
        examples=["26.5.1"],
    )
    embedding_model: str = Field(
        description="Provider + model used to embed every chunk.",
        examples=["openai:text-embedding-3-small"],
    )
    answer_model: str = Field(
        description="Provider + model used by the RAG answerer.",
        examples=["anthropic:claude-sonnet-4-6"],
    )
    answer_fallback_model: str = Field(
        description="Secondary model used when the primary errors out. Empty disables the fallback.",
        examples=["openai:gpt-4o"],
    )
    vector_store: str = Field(
        description="Backend used for vector search.",
        examples=["sqlite-vec", "pgvector"],
    )
    eda_adapter: str = Field(
        description="Event-driven adapter backing the lifecycle topics.",
        examples=["postgres", "redis", "kafka"],
    )
