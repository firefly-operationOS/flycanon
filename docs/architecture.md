# Architecture

flycanon is a single-process Python service composed of four layers,
each implemented as a flat package under `src/flycanon`:

```
interfaces/   public DTOs + enums on the wire
web/          REST controllers + exception advice
core/         configuration + services + CQRS handlers + mappers
models/       SQLAlchemy entities + repositories
```

The framework runtime lives in
[`fireflyframework-pyfly`](https://github.com/fireflyframework/fireflyframework-pyfly)
(DI, CQRS, EDA, web, observability, resilience, actuator). The
agentic substrate -- FireflyAgent over pydantic-ai, the hybrid
retrieval primitives, the corpus + sqlite-vec store -- lives in
[`fireflyframework-agentic`](https://github.com/fireflyframework/fireflyframework-agentic).
flycanon's job is the composition: take raw bytes, ground them in a
canonical version chain, and expose retrieval + RAG over the result.

## Data model

```
canon_sources           one row per inbound artefact (no bytes stored)
  -> canon_chunks       N rows per source, the retrieval-grade fragments
canon_candidates        pre-canonical LLM proposals tied to a source
canon_knowledge_items   canonical pointer (status, current_version, domain, jurisdiction)
  -> canon_knowledge_versions   per-revision content rows, append-only
       -> canon_citations       (version, chunk, source) edges
canon_audit_events      append-only mutation trail
canon_taxonomy_nodes    domain / jurisdiction tree (closure via parent_id)
```

All tables are prefixed `canon_` so a multi-service Postgres remains
auditable. Knowledge content is never mutated in place -- updates
append a new `canon_knowledge_versions` row and flip the previous one
to `superseded`. The corpus / vector projections live alongside in
a single SQLite file (`FLYCANON_CORPUS_PATH`) -- on-prem deployments
that outgrow it flip `FLYCANON_VECTOR_STORE=pgvector` to move the
dense projection into the operational Postgres.

## The seven workshop features

flycanon owns the data plane for the canonical knowledge fabric the
Canon workshop synthesised. The service surfaces are:

| Workshop feature | flycanon surface |
|------------------|------------------|
| F1 Unified Information Repository | `/api/v1/sources` + `canon_sources` / `canon_chunks` |
| F2 Knowledge Extraction & Consolidation | `/api/v1/candidates:propose` + the consolidation prompt over FireflyAgent |
| F3 Automatic Regeneration & Validation | candidate lifecycle (`accept` / `reject`) + the knowledge-version chain |
| F4 Change Traceability | `canon_citations` + `canon_audit_events` + provenance endpoint |
| F5 Active Monitoring (SOTA) | out of scope -- different sibling service |
| F6 Knowledge Visualisation | out of scope -- UI consumer of this API |
| F7 Knowledge Inbox | out of scope -- UI consumer of this API |

flycanon stops at the data plane. UIs, workflow surfaces, and SOTA
fetchers are downstream consumers; they subscribe to the
`flycanon.knowledge` / `flycanon.ingest` / `flycanon.audit` events
and call the REST API.

## Layers

```
                        ┌─────────────────────────────────────────┐
                        │            web/controllers              │
                        │  rest_controller + DefaultCommand/Query │
                        │             advice/exception            │
                        └────────────┬────────────────────────────┘
                                     │ DefaultCommandBus / DefaultQueryBus
                                     ▼
                ┌──────────────────────────────────────────────────────────┐
                │                core/services/* handlers                  │
                │  @command_handler / @query_handler + frozen Command dt   │
                └────────────┬──────────────────────────────────────────┬──┘
                             │                                          │
                             ▼                                          ▼
        ┌────────────────────────────────────┐         ┌─────────────────────────────┐
        │  service layer (no IO leaks here)  │         │  intake / consolidation /   │
        │  KnowledgeService, AuditService,   │         │  retrieval / query services │
        │  TaxonomyService, ProvenanceSvc    │         │  -> agentic primitives      │
        └─────────────┬──────────────────────┘         └─────────────┬───────────────┘
                      │                                              │
                      ▼                                              ▼
        ┌────────────────────────────┐                   ┌──────────────────────────┐
        │  models/repositories       │                   │   SqliteCorpus + sqlite- │
        │  AsyncEngine + Repository  │                   │   vec + HybridRetriever  │
        │  shared across the layer   │                   │   (fireflyframework_…)   │
        └────────────────────────────┘                   └──────────────────────────┘
                      │                                              │
                      ▼                                              ▼
                Postgres (asyncpg)                            SQLite (or pgvector)
```

## DI wiring

[`core/configuration.py`](../src/flycanon/core/configuration.py) is
the **single** place outside the stereotype decorators where pyfly
beans are declared. It registers:

* settings (env-driven `CanonSettings` singleton)
* six repositories sharing one async engine
* SqlAlchemyHealthIndicator -> `/actuator/health`
* loader registry, chunker, IngestionService
* EmbeddingService (provider switch via FLYCANON_EMBEDDING_MODEL)
* CorpusContext + IndexService + RetrievalService
* AuditService, KnowledgeService, ProvenanceService, TaxonomyService
* Consolidator + CandidateService
* SearchService + AnswerService
* IntakeService (the end-to-end orchestrator)

EventPublisher is injected upstream by pyfly's `EdaAutoConfiguration`
(Postgres outbox by default; flip `FLYCANON_EDA_ADAPTER` to memory /
redis / kafka).

## Cross-cutting concerns

* **Observability**: pyfly's tracing + correlation filter installs
  `traceparent`, `tracestate`, `X-Correlation-Id`, `X-Request-Id`,
  `X-Tenant-Id` on every request. The audit log records the
  correlation id on every row.
* **Resilience**: the answer service falls back to
  `FLYCANON_ANSWER_FALLBACK_MODEL` on primary-model failure. EDA
  publish failures are logged but never abort a mutation -- the
  durable trail lives in Postgres.
* **Security**: optional static API keys via `FLYCANON_API_KEYS`. The
  OAuth2 resource-server stack inherited from pyfly is available
  (set `pyfly.security.oauth2.resource-server.enabled=true`).
