# Copyright 2026 Firefly Software Solutions Inc
"""Runtime settings for flycanon.

Settings are loaded from the environment under the ``FLYCANON_`` prefix
(see ``env_template``). The same settings instance is shared across the
FastAPI process and the worker process so the two paths behave
identically.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CanonSettings(BaseSettings):
    """Every knob that affects runtime behaviour."""

    model_config = SettingsConfigDict(
        env_prefix="FLYCANON_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Service --------------------------------------------------------
    log_level: str = "INFO"
    port: int = 8500

    # -- Persistence ----------------------------------------------------
    database_url: str = "postgresql+asyncpg://canon:canon@localhost:5432/flycanon"

    # -- Queue / EDA ----------------------------------------------------
    # The actual EventPublisher is built by pyfly's EdaAutoConfiguration
    # from ``pyfly.eda.*`` properties (see ``pyfly.yaml``). The value
    # here only drives ``${FLYCANON_EDA_ADAPTER}`` interpolation in that
    # file. Default ``postgres`` because the service already runs
    # Postgres for persistence -- no extra broker is required.
    eda_adapter: str = Field(default="postgres", description="memory | postgres | redis | kafka")
    redis_url: str = "redis://localhost:6379/0"

    # Topics for the three event families flycanon publishes. Downstream
    # services subscribe to these to keep their projections in sync.
    ingest_topic: str = "flycanon.ingest"
    knowledge_topic: str = "flycanon.knowledge"
    audit_topic: str = "flycanon.audit"

    # Event-type names for each broadcast. Kept stable -- consumers
    # pattern-match on these.
    source_ingested_event: str = "SourceIngested"
    source_ingestion_failed_event: str = "SourceIngestionFailed"
    knowledge_published_event: str = "KnowledgeItemPublished"
    knowledge_superseded_event: str = "KnowledgeItemSuperseded"
    knowledge_retired_event: str = "KnowledgeItemRetired"
    candidate_proposed_event: str = "CandidateProposed"
    candidate_accepted_event: str = "CandidateAccepted"
    candidate_rejected_event: str = "CandidateRejected"
    audit_event: str = "AuditEventRecorded"

    # Async-ingest worker budget.
    ingest_max_attempts: int = 3
    ingest_timeout_s: int = 600
    retry_base_delay_s: float = 5.0
    retry_max_delay_s: float = 300.0

    # -- Embeddings + RAG ----------------------------------------------
    # Embedding provider identifier in
    # ``<provider>:<model>`` form. Any value
    # ``fireflyframework_agentic.embeddings`` understands works.
    embedding_model: str = Field(
        default="openai:text-embedding-3-small",
        description=(
            "Embedding provider + model in ``<provider>:<model>`` form. "
            "Used by the chunk indexer and the query stage. Must match "
            "``embedding_dimensions`` below."
        ),
    )
    embedding_dimensions: int = Field(
        default=1536,
        ge=64,
        le=4096,
        description="Vector size for the configured embedding model.",
    )
    embedding_batch_size: int = Field(default=64, ge=1, le=2048)

    # Answer-stage model used by the RAG query endpoint.
    answer_model: str = "anthropic:claude-sonnet-4-6"
    answer_fallback_model: str | None = "openai:gpt-4o"

    # Hybrid retrieval knobs. The retriever fuses BM25 + vector ranks
    # via Reciprocal Rank Fusion with parameter ``k`` (``rrf_k``).
    retrieval_top_k: int = Field(default=10, ge=1, le=200)
    retrieval_per_query_k: int = Field(default=30, ge=1, le=500)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)

    # -- Vector store ---------------------------------------------------
    # ``sqlite-vec`` keeps the deployment single-node-friendly. Switch
    # to ``pgvector`` and install the ``pgvector`` extra when the
    # corpus exceeds what one SQLite file can serve comfortably.
    vector_store: str = Field(
        default="sqlite-vec",
        description="``sqlite-vec`` (default) or ``pgvector``.",
    )
    corpus_path: str = Field(
        default="./local_data/corpus.db",
        description="SQLite path used by the default sqlite-vec backend.",
    )

    # -- Ingestion ------------------------------------------------------
    chunk_size_tokens: int = Field(default=1200, ge=128, le=8192)
    chunk_overlap_tokens: int = Field(default=150, ge=0, le=1024)
    chunk_strategy: str = Field(
        default="paragraph",
        description="``token`` | ``sentence`` | ``paragraph``.",
    )
    max_bytes: int = 32 * 1024 * 1024  # 32 MiB

    # -- Security -------------------------------------------------------
    api_keys: str | None = Field(
        default=None,
        description="Comma-separated list of static API keys that grant access. None = unauthenticated.",
    )

    @property
    def api_key_set(self) -> set[str]:
        if not self.api_keys:
            return set()
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache(maxsize=1)
def get_settings() -> CanonSettings:
    """Cached settings accessor.

    Tests reset it with ``get_settings.cache_clear()`` after
    monkey-patching env.
    """
    return CanonSettings()
