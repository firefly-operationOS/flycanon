# Copyright 2026 Firefly Software Solutions Inc
"""``@configuration`` class -- every cross-cutting service as a pyfly bean.

Pyfly's container scans this module, sees the ``@configuration`` class,
instantiates it once, then calls each ``@bean`` method to produce the
beans. The return-type annotation determines the bean's interface so
constructor-injection works on every consumer (controller, command /
query handler, worker).

This is the **single** declaration point for everything that is not
picked up by a stereotype decorator
(``@service``/``@rest_controller``/``@command_handler``/
``@query_handler``/``@repository``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from pyfly.container import bean, configuration
from pyfly.data.relational.health import SqlAlchemyHealthIndicator

from flycanon.config import CanonSettings, get_settings

# AuditService / KnowledgeService / CandidateService / IntakeService /
# TaxonomyService / ProvenanceService are no longer constructed via
# @bean factories here -- they carry their own ``@service`` decorators
# and are auto-discovered via ``scan_packages``. The imports are
# retained for the type hints used in the remaining @bean signatures.
from flycanon.core.services.audit import AuditService  # noqa: F401
from flycanon.core.services.auth.agent_token_service import (
    AgentTokenService,
    RateLimiter,
    _RateLimiter,
)
from flycanon.core.services.auth.redis_rate_limiter import RedisRateLimiter
from flycanon.core.services.binary import (
    GotenbergConverter,
    LibreOfficeConverter,
    OfficeConverter,
)
from flycanon.core.services.binary.office_converter import NoOpOfficeConverter
from flycanon.core.services.consolidation import (
    Consolidator,
)
from flycanon.core.services.consolidation.prompt_loader import load_prompt
from flycanon.core.services.embeddings import EmbeddingService, build_embedding_service
from flycanon.core.services.ingestion import (
    Chunker,
    IngestionService,
    LoaderRegistry,
)
from flycanon.core.services.ingestion.chunker import build_default_chunker
from flycanon.core.services.ingestion.loaders import default_registry
from flycanon.core.services.query import AnswerService, SearchService
from flycanon.core.services.retrieval import (
    CorpusContext,
    IndexService,
    RetrievalService,
    build_corpus_context,
)
from flycanon.core.services.retrieval.query_expander import QueryExpander
from flycanon.core.services.retrieval.reranker import build_reranker
from flycanon.models.repositories import (
    AgentTokenRepository,
    AuditRepository,
    CandidateRepository,
    ChunkRepository,
    ConversationRepository,
    CostRepository,
    IngestJobRepository,
    KnowledgeRepository,
    RelationRepository,
    SourceRepository,
    TaxonomyRepository,
    WorkspaceRepository,
)
from flycanon.web.conventions.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from flycanon.web.conventions.redis_idempotency import RedisIdempotencyStore

logger = logging.getLogger(__name__)


@configuration
class CanonCoreConfiguration:
    """Wiring for everything outside the pyfly stereotype decorators.

    ``EventPublisher``-dependent services (``AuditService``,
    ``KnowledgeService``, ``CandidateService``, ``IntakeService``,
    ``TaxonomyService``) are declared as ``@service`` classes on the
    classes themselves so pyfly resolves them lazily -- by then
    pyfly's ``EdaAutoConfiguration`` has already registered the
    ``EventPublisher`` bean.
    """

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @bean
    def settings(self) -> CanonSettings:
        return get_settings()

    # ------------------------------------------------------------------
    # Repositories -- one async engine, six repositories sharing it
    # ------------------------------------------------------------------

    @bean
    def source_repository(self, settings: CanonSettings) -> SourceRepository:
        return SourceRepository.from_url(settings.database_url)

    @bean
    def chunk_repository(self, settings: CanonSettings) -> ChunkRepository:
        return ChunkRepository.from_url(settings.database_url)

    @bean
    def knowledge_repository(self, settings: CanonSettings) -> KnowledgeRepository:
        return KnowledgeRepository.from_url(settings.database_url)

    @bean
    def candidate_repository(self, settings: CanonSettings) -> CandidateRepository:
        return CandidateRepository.from_url(settings.database_url)

    @bean
    def audit_repository(self, settings: CanonSettings) -> AuditRepository:
        return AuditRepository.from_url(settings.database_url)

    @bean
    def taxonomy_repository(self, settings: CanonSettings) -> TaxonomyRepository:
        return TaxonomyRepository.from_url(settings.database_url)

    @bean
    def relation_repository(self, settings: CanonSettings) -> RelationRepository:
        return RelationRepository.from_url(settings.database_url)

    @bean
    def ingest_job_repository(self, settings: CanonSettings) -> IngestJobRepository:
        return IngestJobRepository.from_url(settings.database_url)

    @bean
    def conversation_repository(self, settings: CanonSettings) -> ConversationRepository:
        return ConversationRepository.from_url(settings.database_url)

    @bean
    def cost_repository(self, settings: CanonSettings) -> CostRepository:
        return CostRepository.from_url(settings.database_url)

    @bean
    def workspace_repository(self, settings: CanonSettings) -> WorkspaceRepository:
        return WorkspaceRepository.from_url(settings.database_url)

    @bean
    def agent_token_repository(self, settings: CanonSettings) -> AgentTokenRepository:
        return AgentTokenRepository.from_url(settings.database_url)

    # ------------------------------------------------------------------
    # Auth services
    # ------------------------------------------------------------------

    @bean
    def rate_limiter(self, settings: CanonSettings) -> RateLimiter:
        """Per-token sliding-window rate limiter.

        Backend selection:

        * ``FLYCANON_RATE_LIMIT_BACKEND=redis`` -- always Redis.
        * ``FLYCANON_RATE_LIMIT_BACKEND=in_memory`` -- always
          in-memory (even when ``redis_url`` is set).
        * unset / ``auto`` (the default) -- Redis when ``redis_url``
          is configured, in-memory otherwise.

        In-memory is fine for single-replica dev / test posture but
        does NOT enforce ``rate_limit_rpm`` correctly across replicas;
        production deployments behind a load balancer MUST use the
        Redis variant or the per-replica buckets effectively multiply
        the budget by the replica count.
        """
        if _use_redis(settings, "rate_limit_backend"):
            client = _build_redis_client(settings)
            return RedisRateLimiter(client)
        return _RateLimiter()

    @bean
    def agent_token_service(
        self,
        agent_token_repository: AgentTokenRepository,
        rate_limiter: RateLimiter,
    ) -> AgentTokenService:
        return AgentTokenService(agent_token_repository, rate_limiter=rate_limiter)

    # ------------------------------------------------------------------
    # Replay dedup for the agent surface
    #
    # A singleton :class:`IdempotencyStore` bean is shared by every
    # agent controller so a replayed POST with the same
    # ``Idempotency-Key`` returns the original response body without
    # re-dispatching the command. Backend selection mirrors
    # :meth:`rate_limiter`: Redis when ``redis_url`` is set (or
    # ``FLYCANON_IDEMPOTENCY_BACKEND=redis``), in-memory otherwise.
    # ------------------------------------------------------------------

    @bean
    def idempotency_store(self, settings: CanonSettings) -> IdempotencyStore:
        if _use_redis(settings, "idempotency_backend"):
            client = _build_redis_client(settings)
            return RedisIdempotencyStore(client)
        return InMemoryIdempotencyStore()

    @bean(name="database_health")
    def database_health(self, source_repository: SourceRepository) -> SqlAlchemyHealthIndicator:
        """SQLAlchemy health probe -- wired into pyfly's /actuator/health."""
        return SqlAlchemyHealthIndicator(source_repository.engine)

    # ------------------------------------------------------------------
    # Ingestion + embeddings + retrieval
    # ------------------------------------------------------------------

    @bean
    def loader_registry(self) -> LoaderRegistry:
        return default_registry()

    @bean
    def chunker(self, settings: CanonSettings) -> Chunker:
        return build_default_chunker(
            chunk_size_tokens=settings.chunk_size_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )

    @bean
    def ingestion_service(
        self,
        loader_registry: LoaderRegistry,
        chunker: Chunker,
    ) -> IngestionService:
        return IngestionService(loaders=loader_registry, chunker=chunker)

    @bean
    def embedding_service(self, settings: CanonSettings) -> EmbeddingService:
        return build_embedding_service(
            embedding_model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
        )

    @bean
    def corpus_context(self, settings: CanonSettings) -> CorpusContext:
        ctx = build_corpus_context(settings=settings)
        # Eagerly initialise so the first request doesn't pay the
        # schema-creation cost (and so misconfigured paths fail at
        # boot, not at first hit). The RuntimeError branch covers
        # the cold-start case where no running loop exists yet --
        # pyfly will drive initialisation when the app starts.
        with contextlib.suppress(RuntimeError):
            asyncio.get_event_loop().run_until_complete(ctx.initialise())
        return ctx

    @bean
    def index_service(self, corpus_context: CorpusContext) -> IndexService:
        return IndexService(context=corpus_context)

    @bean
    def retrieval_service(
        self,
        corpus_context: CorpusContext,
        embedding_service: EmbeddingService,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        knowledge_repository: KnowledgeRepository,
        settings: CanonSettings,
    ) -> RetrievalService:
        # Optional retrieval enhancements driven by env config:
        # * Reranker -- cross-encoder over the fused hit set
        #   (FLYCANON_RERANKER_MODEL = "" disables it). Adapters
        #   for Cohere + Voyage ship; falling back to NoOp when
        #   the API key is missing keeps the path resilient.
        # * Query expander -- LLM paraphrase + multi-query RRF
        #   fusion (FLYCANON_QUERY_EXPANSION_ENABLED=true). Each
        #   query expansion costs one extra LLM call so off by
        #   default.
        reranker = build_reranker(settings.reranker_model)
        expander = (
            QueryExpander(model=settings.answer_model, settings=settings)
            if settings.query_expansion_enabled
            else None
        )
        return RetrievalService(
            context=corpus_context,
            embeddings=embedding_service,
            source_repository=source_repository,
            chunk_repository=chunk_repository,
            knowledge_repository=knowledge_repository,
            default_top_k=settings.retrieval_top_k,
            default_per_query_k=settings.retrieval_per_query_k,
            rrf_k=settings.retrieval_rrf_k,
            reranker=reranker,
            reranker_top_n=settings.reranker_top_n,
            query_expander=expander,
            query_expansion_n=settings.query_expansion_n if expander else 1,
        )

    # ------------------------------------------------------------------
    # Audit + knowledge lifecycle + taxonomy
    #
    # AuditService, KnowledgeService, ProvenanceService, TaxonomyService
    # are now ``@service``-decorated classes auto-discovered via
    # ``scan_packages``. Pyfly's container resolves their dependencies
    # (including ``EventPublisher`` from ``EdaAutoConfiguration``) at
    # first lookup -- by that point every auto-configuration has run.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Consolidation + query
    # ------------------------------------------------------------------

    @bean
    def consolidator(
        self,
        settings: CanonSettings,
    ) -> Consolidator:
        # Prompt is loaded inline so the ``PromptTemplate`` bean type
        # doesn't collide with the answer-prompt bean -- pyfly's
        # container resolves @bean params by TYPE, so two beans
        # returning the same ``PromptTemplate`` type would overwrite
        # each other under the same key.
        #
        # The ``settings`` are threaded into the Consolidator so the
        # central agent builder can read the
        # ``agent_max_output_tokens`` / ``consolidator_max_output_tokens``
        # knobs -- avoids the 4096 default truncating structured
        # multi-candidate arrays mid-emit.
        return Consolidator(
            prompt=load_prompt("consolidation"),
            default_model=settings.answer_model,
            settings=settings,
        )

    # CandidateService is ``@service``-decorated; auto-discovered.

    @bean
    def search_service(self, retrieval_service: RetrievalService) -> SearchService:
        return SearchService(retrieval=retrieval_service)

    @bean
    def answer_service(
        self,
        retrieval_service: RetrievalService,
        settings: CanonSettings,
    ) -> AnswerService:
        # Prompt is loaded inline -- see ``consolidator`` above for the
        # rationale (avoid type-based DI collision on PromptTemplate).
        # ``settings`` are threaded through so the central agent
        # builder can honor the ``answer_max_output_tokens`` /
        # ``agent_max_output_tokens`` knobs.
        return AnswerService(
            retrieval=retrieval_service,
            prompt=load_prompt("answer"),
            default_model=settings.answer_model,
            fallback_model=settings.answer_fallback_model,
            settings=settings,
        )

    # ------------------------------------------------------------------
    # Binary normalisation
    #
    # PdfGuard, ImageNormalizer, ArchiveUnpacker, EmailUnpacker, and
    # BinaryNormalizer itself carry ``@service`` decorators and are
    # autoscanned via the ``flycanon.core.services`` scan path -- they
    # don't need explicit @bean factories.
    #
    # The OfficeConverter selection is settings-driven so we keep the
    # bean factory; the BinaryNormalizer takes it as a constructor
    # arg through pyfly's auto-resolution against the
    # :class:`OfficeConverter` protocol.
    # ------------------------------------------------------------------

    @bean
    def office_converter(self, settings: CanonSettings) -> OfficeConverter:
        kind = (settings.office_converter or "none").lower()
        if kind in {"none", "", "disabled"}:
            return NoOpOfficeConverter()
        if kind == "gotenberg":
            return GotenbergConverter(settings=settings)
        if kind == "libreoffice":
            return LibreOfficeConverter(settings=settings)
        raise ValueError(
            f"unknown FLYCANON_OFFICE_CONVERTER={settings.office_converter!r}; "
            "expected 'none', 'gotenberg', or 'libreoffice'"
        )

    # IntakeService is ``@service``-decorated; auto-discovered.


# ----------------------------------------------------------------------
# Redis adapter helpers
#
# Shared by :meth:`CanonCoreConfiguration.rate_limiter` and
# :meth:`CanonCoreConfiguration.idempotency_store`. Build one async
# Redis client per process and reuse it across adapters so both share
# the connection pool.
# ----------------------------------------------------------------------


def _use_redis(settings: CanonSettings, backend_field: str) -> bool:
    """Return ``True`` when the matching adapter should use Redis.

    Three inputs decide:

    * ``settings.<backend_field>`` -- explicit env override
      (``redis`` / ``in_memory`` / ``auto``).
    * ``settings.redis_url`` -- when set, Redis is the default.
    * ``backend_field`` -- the per-adapter knob name; lets the
      rate-limit and idempotency stores be configured independently.

    ``auto`` (the default) picks Redis if ``redis_url`` is set, else
    falls back to in-memory. Explicit values always win so operators
    can force one or the other (e.g. for local dev with a real Redis
    but in-memory rate limiting, or vice versa).
    """
    explicit = getattr(settings, backend_field, "auto") or "auto"
    explicit = explicit.lower()
    if explicit == "redis":
        return True
    if explicit in {"in_memory", "memory"}:
        return False
    return bool(settings.redis_url)


def _build_redis_client(settings: CanonSettings):  # noqa: ANN202
    """Async Redis client bound to ``settings.redis_url``.

    Returns the client untyped so the import of ``redis.asyncio``
    stays local to this helper -- callers receive a duck-typed handle
    that satisfies :class:`RedisRateLimiter` / :class:`RedisIdempotencyStore`
    constructors.

    Decoding is left to the adapters: :class:`RedisIdempotencyStore`
    handles both ``bytes`` and ``str`` responses so the helper does
    not need to set ``decode_responses=True``.
    """
    from redis.asyncio import from_url as _redis_from_url

    return _redis_from_url(settings.redis_url)
