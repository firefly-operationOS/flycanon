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
# AuditService / KnowledgeService / CandidateService / IntakeService /
# TaxonomyService / ProvenanceService are no longer constructed via
# @bean factories here -- they carry their own ``@service`` decorators
# and are auto-discovered via ``scan_packages``. The imports are
# retained for the type hints used in the remaining @bean signatures.
from flycanon.core.services.audit import AuditService  # noqa: F401
from flycanon.core.services.binary import (
    BinaryNormalizer,
    GotenbergConverter,
    LibreOfficeConverter,
    OfficeConverter,
)
from flycanon.core.services.binary.office_converter import NoOpOfficeConverter
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
    RelationRepository,
    SourceRepository,
    TaxonomyRepository,
)

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
