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

"""Runtime settings for flycanon.

Settings are loaded from the environment under the ``FLYCANON_`` prefix
(see ``env_template``). The same settings instance is shared across the
FastAPI process and the worker process so the two paths behave
identically.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
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
    # Empty default means "no Redis available" so the rate-limit +
    # idempotency adapters fall back to the in-memory variants unless
    # an operator explicitly opts into Redis by exporting
    # ``FLYCANON_REDIS_URL``. Setting a localhost default here would
    # cause every CI run + container that lacks a Redis sidecar to
    # crash on the first agent-token revoke ("Connect call failed
    # ('127.0.0.1', 6379)").
    redis_url: str = ""

    # -- Rate limiter + idempotency backend selection -------------------
    # Per-adapter overrides for the auth + replay surfaces. ``auto``
    # (the default) picks Redis when ``redis_url`` is set, else falls
    # back to the in-process implementation. ``redis`` forces Redis;
    # ``in_memory`` forces the in-process variant even when
    # ``redis_url`` is configured (useful for local dev with a shared
    # Redis instance but per-process MVP semantics). See
    # :func:`flycanon.core.configuration._use_redis` for the
    # resolution.
    rate_limit_backend: str = Field(
        default="auto",
        description="``auto`` | ``redis`` | ``in_memory``.",
    )
    idempotency_backend: str = Field(
        default="auto",
        description="``auto`` | ``redis`` | ``in_memory``.",
    )

    # Topics for the event families flycanon publishes. Downstream
    # services subscribe to these to keep their projections in sync.
    ingest_topic: str = "flycanon.ingest"
    knowledge_topic: str = "flycanon.knowledge"
    audit_topic: str = "flycanon.audit"
    # Workspace lifecycle topic. The ``v1`` suffix is the contract
    # version; future incompatible changes go on a parallel topic
    # (``canon.workspaces.v2``) so consumers can migrate gradually.
    workspace_topic: str = "canon.workspaces.v1"

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
    workspace_created_event: str = "WorkspaceCreated"
    workspace_updated_event: str = "WorkspaceUpdated"
    workspace_deleted_event: str = "WorkspaceDeleted"

    # Async-ingest worker budget.
    ingest_max_attempts: int = 3
    ingest_timeout_s: int = 600
    retry_base_delay_s: float = 5.0
    retry_max_delay_s: float = 300.0

    # Worker concurrency knobs. The IngestWorker subscribes to four
    # event families (Source*, Knowledge*, Candidate*, audit) and
    # dispatches each delivery through a bounded semaphore so a burst
    # of events doesn't blow up an LLM provider's rate limit or
    # exhaust the async pool. Handlers run with a wall-clock timeout
    # so a stuck downstream call can't pin a worker slot indefinitely;
    # graceful shutdown waits ``worker_shutdown_grace_s`` for inflight
    # tasks to drain before cancelling.
    worker_max_concurrency: int = Field(default=8, ge=1, le=128)
    worker_handler_timeout_s: float = Field(default=120.0, gt=0.0)
    worker_shutdown_grace_s: float = Field(default=30.0, ge=0.0)

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

    # Output-token budget for every FireflyAgent we build. Anthropic's
    # API defaults to ``max_tokens=4096`` and OpenAI clamps similarly --
    # too tight for the consolidation stage (which emits structured
    # multi-candidate arrays running 6-10k tokens for dense business
    # docs) and the answer stage (long multi-paragraph answers with
    # citations). 8192 is the public ceiling for Sonnet 4.6 and
    # matches the budget flydocs/flyradar use for their structured
    # extractors; raising it stops the model from silently truncating
    # mid-array and falling back to empty results. Operators on
    # models with a higher ceiling (e.g. Sonnet 4.6 with the long
    # output beta, or GPT-4o with the 16k window) can bump it.
    agent_max_output_tokens: int = Field(default=8192, ge=512, le=64000)
    # Per-stage overrides for when a specific stage needs more room
    # than the global default (or wants to cap below it). ``None``
    # falls back to ``agent_max_output_tokens``.
    consolidator_max_output_tokens: int | None = Field(default=None)
    answer_max_output_tokens: int | None = Field(default=None)

    # Hybrid retrieval knobs. The retriever fuses BM25 + vector ranks
    # via Reciprocal Rank Fusion with parameter ``k`` (``rrf_k``).
    retrieval_top_k: int = Field(default=10, ge=1, le=200)
    retrieval_per_query_k: int = Field(default=30, ge=1, le=500)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)

    # Optional cross-encoder reranker. Empty value disables it
    # (the default). Accepts ``cohere:rerank-multilingual-v3.0``,
    # ``voyageai:rerank-2``, or another supported model id. The
    # provider's API key is read from the standard environment
    # variable (COHERE_API_KEY / VOYAGEAI_API_KEY).
    reranker_model: str = Field(default="", description="Empty = disabled.")
    # Candidates sent to the reranker per query -- we widen the
    # post-fusion top-N to give the cross-encoder room to surface
    # buried gems, then trim back to ``retrieval_top_k`` after.
    reranker_top_n: int = Field(default=20, ge=1, le=200)

    # Optional LLM-driven query expansion. The pre-retrieval stage
    # asks the answer model for N paraphrases of the user query,
    # runs each through the retriever, and RRF-fuses the result
    # lists for higher recall. Costs an extra LLM call per query
    # so off by default; flip on for high-stakes corpora where
    # missed hits hurt more than answer latency.
    query_expansion_enabled: bool = Field(default=False)
    query_expansion_n: int = Field(default=3, ge=1, le=10)

    # PII detection at ingest.
    #   scanner: ``regex`` (default, no deps) | ``presidio`` (needs
    #            the optional presidio-analyzer extra) | ``disabled``.
    #   policy:  ``warn`` (default -- index as-is, flag the source
    #            row's metadata for audit review) | ``redact``
    #            (replace each hit with [REDACTED:<KIND>] before
    #            chunking) | ``reject`` (fail the intake with
    #            ``pii_detected``).
    pii_scanner: str = Field(default="regex")
    pii_policy: str = Field(default="warn")

    # -- Vector store ---------------------------------------------------
    # Backend selector for the DENSE half of hybrid retrieval. The lexical
    # BM25 half always rides on the ``tsv`` column of ``canon_chunks`` on the
    # canonical Postgres instance; only the dense projection is pluggable.
    # ``pgvector`` (default) co-locates dense vectors with Postgres; ``qdrant``
    # and ``chroma`` use the adapters that ship in fireflyframework-agentic.
    # Whatever the backend, isolation is enforced per ``(tenant_id,
    # workspace_id)`` by the scoped vector-store wrapper.
    vector_store: str = Field(
        default="pgvector",
        description="Dense-vector backend: ``pgvector`` (default), ``qdrant``, or ``chroma``.",
    )

    # -- pgvector backend ----------------------------------------------
    pgvector_table: str = "canon_chunk_vectors"
    pgvector_hnsw_m: int = Field(default=16, ge=4, le=64)
    pgvector_hnsw_ef_construction: int = Field(default=64, ge=8, le=1024)
    # ``hnsw.ef_search`` set per query: higher = better recall, more latency.
    pgvector_hnsw_ef_search: int = Field(default=200, ge=1, le=1000)

    # -- qdrant backend (FLYCANON_VECTOR_STORE=qdrant) -----------------
    # Requires ``uv sync --extra qdrant``. Self-hosted or Qdrant Cloud.
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str | None = Field(default=None)
    qdrant_collection: str = Field(default="canon_vectors")

    # -- chroma backend (FLYCANON_VECTOR_STORE=chroma) -----------------
    # Requires ``uv sync --extra chroma``. An empty ``chroma_host`` uses an
    # in-process ephemeral client (dev/test); set it for a Chroma server.
    chroma_host: str = Field(default="")
    chroma_port: int = Field(default=8000, ge=1, le=65535)
    chroma_collection: str = Field(default="canon_vectors")
    # BM25 / Postgres FTS text-search configuration. ``simple`` is
    # the safest multilingual default (no stemming, no stopwords);
    # switch to ``english`` / ``spanish`` / etc. when the deployment
    # is mono-lingual to get language-aware stemming.
    bm25_text_search_config: str = Field(
        default="simple",
        description=(
            "Postgres ``text-search`` configuration used by the BM25 "
            "projection on ``canon_chunks.tsv``. ``simple`` for "
            "multilingual corpora; ``english`` / ``spanish`` / etc. "
            "for language-specific stemming."
        ),
    )

    # -- RLM engine -----------------------------------------------------
    # The Recursive Language Model query engine (``core/services/query/
    # rlm/``) is a CodeAct REPL: a root orchestrator that writes Python
    # against the document corpus, makes recursive sub-calls on slices,
    # and finishes by citing the filings/pages it used. All three models
    # are in ``<provider>:<model>`` form; the ``anthropic:`` prefix is
    # stripped before the id is sent to the Anthropic Messages API.
    #
    # ``answer_mode`` (FLYCANON_ANSWER_MODE) selects which engine the
    # non-streaming ``/api/v1/query`` answer path uses: ``rlm`` (default)
    # routes to the Recursive Language Model answerer, ``rag`` routes to
    # the legacy hybrid-retrieval :class:`AnswerService`. RAG is opt-in
    # and deprecated -- the :class:`AnswerDispatcher` logs a deprecation
    # warning whenever it is selected. The value is normalised to
    # lowercase; any value other than ``rag`` falls back to ``rlm``.
    answer_mode: str = Field(
        default="rlm",
        description="Answer engine for the non-streaming query path: ``rlm`` (default) or ``rag``.",
    )
    rlm_root_model: str = Field(
        default="anthropic:claude-sonnet-4-6",
        description="Orchestrator model that drives the CodeAct REPL loop.",
    )
    rlm_sub_model: str = Field(
        default="anthropic:claude-sonnet-4-6",
        description="Model for flat recursive sub-calls made from REPL code.",
    )
    rlm_answer_model: str = Field(
        default="anthropic:claude-sonnet-4-6",
        description="Model for the final single-shot answer synthesis.",
    )
    # Max orchestrator turns before the loop gives up and asks for a
    # plain-text answer from the transcript.
    rlm_max_iters: int = Field(default=8, ge=1, le=64)
    # Total recursive sub-call budget across one root session.
    rlm_sub_budget: int = Field(default=12, ge=0, le=128)
    # How deep ``rlm(...)`` may nest before it degrades to a flat ``llm``.
    rlm_max_depth: int = Field(default=1, ge=0, le=8)
    # Mark the large, static RLM system prompt with Anthropic
    # ``cache_control: ephemeral`` so it is cached server-side and reused
    # across the many Messages calls one CodeAct session makes, cutting
    # input-token cost and per-call latency. When ``False`` the system
    # prompt is sent as a plain string (no cache breakpoint).
    rlm_prompt_cache: bool = Field(default=True)

    # -- Corpus page cache ----------------------------------------------
    # Shared cache layered over the lazy RLM corpus store: an in-scope
    # filing's original is fetched from the object store + PyMuPDF-
    # extracted at most once per process (in-memory LRU) and once per
    # fleet (shared Redis). Keyed by the source's ``content_sha256`` so a
    # re-ingested source (new bytes -> new sha) misses the stale entry
    # automatically. Backend selection mirrors
    # :func:`flycanon.core.configuration._use_redis`: ``auto`` (the
    # default) uses Redis when ``redis_url`` is set, in-memory otherwise;
    # ``redis`` / ``in_memory`` force one or the other. The Redis client
    # is synchronous (read from the RLM engine's worker thread).
    corpus_cache_backend: str = Field(
        default="auto",
        description="Corpus page-cache backend: ``auto`` | ``redis`` | ``in_memory``.",
    )
    # Per-entry TTL (seconds) for both backends.
    corpus_cache_ttl_s: int = Field(default=3600, ge=0)
    # LRU cap for the in-memory backend (ignored by the Redis backend,
    # which relies on native ``EX`` expiry).
    corpus_cache_max_entries: int = Field(default=512, ge=1)

    # -- Object store ---------------------------------------------------
    # Where the original document bytes an intake submitted are persisted
    # so RLM can later replay them. The port lives in
    # ``core/services/storage/``; ``localfs`` (default) writes files under
    # a root directory for dev / test, ``s3`` (requires ``uv sync --extra
    # s3``) writes to a bucket. AWS credentials are read from the standard
    # environment (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` /
    # profiles / instance roles), not from these settings. Keys follow the
    # ``tenant/workspace/.../files/{id}.{ext}`` layout shared with flyquery.
    object_store_backend: str = Field(
        default="localfs",
        description="Object-store backend: ``localfs`` (default) or ``s3``.",
    )
    # localfs backend (FLYCANON_OBJECT_STORE_LOCALFS_ROOT).
    object_store_localfs_root: str = Field(default="./var/objects")
    # s3 backend (FLYCANON_OBJECT_STORE_S3_*). Requires ``uv sync --extra s3``.
    object_store_s3_bucket: str = Field(default="")
    # Key prefix prepended to every object key within the bucket.
    object_store_s3_prefix: str = Field(default="")
    # Optional custom endpoint for MinIO / S3-compatible services; empty
    # means the default AWS endpoint.
    object_store_s3_endpoint_url: str = Field(default="")
    # Optional region; empty defers to boto3's standard region resolution.
    object_store_s3_region: str = Field(default="")
    # Whether intake persists the original uploaded bytes to the object
    # store and records the key on the source row (``object_store_key``).
    # On by default so RLM has a whole-document corpus to reason over; a
    # write failure is best-effort and never fails the ingest.
    store_originals: bool = Field(default=True)

    # -- Ingestion ------------------------------------------------------
    chunk_size_tokens: int = Field(default=1200, ge=128, le=8192)
    chunk_overlap_tokens: int = Field(default=150, ge=0, le=1024)
    chunk_strategy: str = Field(
        default="paragraph",
        description="``token`` | ``sentence`` | ``paragraph``.",
    )
    max_bytes: int = 32 * 1024 * 1024  # 32 MiB
    # Timeout for URL-fetched source intake (``POST /api/v1/sources``
    # with ``uri`` set instead of ``content_base64``). Origins that
    # take longer than this fail with ``url_fetch_failed`` rather than
    # pinning a request worker indefinitely. Connect timeout is
    # capped at min(total, 10s).
    url_fetch_timeout_s: float = Field(default=60.0, gt=0.0)

    # -- Binary normalisation -------------------------------------------
    # The binary normaliser (``core/services/binary/``) routes every
    # inbound payload through magic-byte sniffing, image format
    # conversion (HEIC / AVIF / TIFF / BMP / SVG -> PNG), archive
    # expansion (ZIP / 7Z / TAR / GZ), email decomposition (EML /
    # MSG), and the optional Office -> PDF converter (Gotenberg or
    # LibreOffice). Set ``binary_normalize_enabled=False`` to bypass
    # the entire stage -- useful for callers that pre-normalise
    # upstream or for debugging.
    binary_normalize_enabled: bool = True
    binary_max_recursion_depth: int = Field(default=4, ge=0, le=10)
    binary_max_expanded_files: int = Field(default=50, ge=1, le=500)
    office_converter: str = Field(
        default="none",
        description=(
            "``none`` (default; the native per-format loaders read DOCX / "
            "XLSX / PPTX / HTML in-process), ``gotenberg`` (HTTP sidecar; "
            "distroless-friendly), or ``libreoffice`` (in-container "
            "subprocess; requires ``soffice`` in the runtime image)."
        ),
    )
    gotenberg_url: str = "http://gotenberg:3000"
    gotenberg_timeout_s: int = 60
    binary_libreoffice_path: str = "soffice"
    binary_libreoffice_timeout_s: int = 60
    ocr_lang: str = Field(
        default="eng+spa",
        description=(
            "Default Tesseract ``-l`` argument used by the ImageLoader. "
            "``+``-joined ISO 639-2/B language codes. The runtime "
            "Dockerfile installs the most common European packs by "
            "default."
        ),
    )

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

    @field_validator("answer_mode", mode="before")
    @classmethod
    def _normalise_answer_mode(cls, value: object) -> str:
        # Normalise to lowercase and treat any unrecognised value as the
        # ``rlm`` default -- only ``rag`` opts into the deprecated path.
        text = str(value).strip().lower() if value is not None else ""
        return "rag" if text == "rag" else "rlm"


@lru_cache(maxsize=1)
def get_settings() -> CanonSettings:
    """Cached settings accessor.

    Tests reset it with ``get_settings.cache_clear()`` after
    monkey-patching env.
    """
    return CanonSettings()
