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
import logging

from pyfly.container import bean, configuration
from pyfly.data.relational.health import SqlAlchemyHealthIndicator

from flycanon.config import CanonSettings, get_settings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.consolidation import (
    CandidateService,
    Consolidator,
)
from flycanon.core.services.consolidation.prompt_loader import PromptTemplate, load_prompt
from flycanon.core.services.embeddings import EmbeddingService, build_embedding_service
from flycanon.core.services.ingestion import (
    Chunker,
    IngestionService,
    LoaderRegistry,
)
from flycanon.core.services.ingestion.chunker import build_default_chunker
from flycanon.core.services.ingestion.loaders import default_registry
from flycanon.core.services.knowledge import KnowledgeService, ProvenanceService
from flycanon.core.services.query import AnswerService, SearchService
from flycanon.core.services.retrieval import (
    CorpusContext,
    IndexService,
    RetrievalService,
    build_corpus_context,
)
from flycanon.core.services.sources import IntakeService
from flycanon.core.services.taxonomy import TaxonomyService
from flycanon.models.repositories import (
    AuditRepository,
    CandidateRepository,
    ChunkRepository,
    KnowledgeRepository,
    SourceRepository,
    TaxonomyRepository,
)

logger = logging.getLogger(__name__)


@configuration
class CanonCoreConfiguration:
    """Wiring for everything outside the pyfly stereotype decorators."""

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
        ctx = build_corpus_context(
            backend=settings.vector_store,
            corpus_path=settings.corpus_path,
            dimensions=settings.embedding_dimensions,
        )
        # Eagerly initialise so the first request doesn't pay the
        # schema-creation cost (and so misconfigured paths fail at
        # boot, not at first hit).
        try:
            asyncio.get_event_loop().run_until_complete(ctx.initialise())
        except RuntimeError:
            # No running loop yet (cold-start); pyfly will drive
            # initialisation when the app starts.
            pass
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
        return RetrievalService(
            context=corpus_context,
            embeddings=embedding_service,
            source_repository=source_repository,
            chunk_repository=chunk_repository,
            knowledge_repository=knowledge_repository,
            default_top_k=settings.retrieval_top_k,
            default_per_query_k=settings.retrieval_per_query_k,
            rrf_k=settings.retrieval_rrf_k,
        )

    # ------------------------------------------------------------------
    # Audit + knowledge lifecycle + taxonomy
    # ------------------------------------------------------------------

    @bean
    def audit_service(
        self,
        audit_repository: AuditRepository,
        settings: CanonSettings,
    ) -> AuditService:
        # EventPublisher is registered upstream by pyfly's
        # EdaAutoConfiguration; we pass ``None`` to the bean factory
        # here -- the framework rebinds the dependency at startup
        # via the dynamic context. AuditService treats the publisher
        # as best-effort, so a None during the bootstrap window is
        # equivalent to "audit-only, no broadcast".
        return AuditService(
            repository=audit_repository,
            event_publisher=None,
            settings=settings,
        )

    @bean
    def knowledge_service(
        self,
        knowledge_repository: KnowledgeRepository,
        audit_service: AuditService,
        settings: CanonSettings,
    ) -> KnowledgeService:
        return KnowledgeService(
            repository=knowledge_repository,
            audit=audit_service,
            event_publisher=None,
            settings=settings,
        )

    @bean
    def provenance_service(
        self,
        knowledge_repository: KnowledgeRepository,
        source_repository: SourceRepository,
    ) -> ProvenanceService:
        return ProvenanceService(
            knowledge_repository=knowledge_repository,
            source_repository=source_repository,
        )

    @bean
    def taxonomy_service(
        self,
        taxonomy_repository: TaxonomyRepository,
        audit_service: AuditService,
        settings: CanonSettings,
    ) -> TaxonomyService:
        return TaxonomyService(
            repository=taxonomy_repository,
            audit=audit_service,
            settings=settings,
        )

    # ------------------------------------------------------------------
    # Consolidation + query
    # ------------------------------------------------------------------

    @bean
    def consolidation_prompt(self) -> PromptTemplate:
        return load_prompt("consolidation")

    @bean
    def consolidator(
        self,
        consolidation_prompt: PromptTemplate,
        settings: CanonSettings,
    ) -> Consolidator:
        return Consolidator(
            prompt=consolidation_prompt,
            default_model=settings.answer_model,
        )

    @bean
    def candidate_service(
        self,
        consolidator: Consolidator,
        candidate_repository: CandidateRepository,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        knowledge_service: KnowledgeService,
        audit_service: AuditService,
        settings: CanonSettings,
    ) -> CandidateService:
        return CandidateService(
            consolidator=consolidator,
            candidate_repository=candidate_repository,
            source_repository=source_repository,
            chunk_repository=chunk_repository,
            knowledge=knowledge_service,
            audit=audit_service,
            event_publisher=None,
            settings=settings,
        )

    @bean
    def search_service(self, retrieval_service: RetrievalService) -> SearchService:
        return SearchService(retrieval=retrieval_service)

    @bean
    def answer_prompt(self) -> PromptTemplate:
        return load_prompt("answer")

    @bean
    def answer_service(
        self,
        retrieval_service: RetrievalService,
        answer_prompt: PromptTemplate,
        settings: CanonSettings,
    ) -> AnswerService:
        return AnswerService(
            retrieval=retrieval_service,
            prompt=answer_prompt,
            default_model=settings.answer_model,
            fallback_model=settings.answer_fallback_model,
        )

    @bean
    def intake_service(
        self,
        ingestion_service: IngestionService,
        embedding_service: EmbeddingService,
        index_service: IndexService,
        source_repository: SourceRepository,
        chunk_repository: ChunkRepository,
        audit_service: AuditService,
        settings: CanonSettings,
    ) -> IntakeService:
        return IntakeService(
            ingestion=ingestion_service,
            embeddings=embedding_service,
            indexer=index_service,
            source_repository=source_repository,
            chunk_repository=chunk_repository,
            audit=audit_service,
            event_publisher=None,
            settings=settings,
        )
