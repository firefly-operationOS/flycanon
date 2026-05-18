# Architecture

flycanon is a single-process Python service composed of four layers,
each implemented as a flat package under `src/flycanon`:

```
interfaces/   public DTOs + enums on the wire
web/          REST controllers + exception advice
core/         configuration + services + CQRS handlers + mappers + binary normaliser
models/       SQLAlchemy entities + repositories
```

The framework runtime lives in
[`fireflyframework-pyfly`](https://github.com/fireflyframework/fireflyframework-pyfly)
(DI, CQRS, EDA, web, observability, resilience, actuator). The
agentic substrate -- FireflyAgent over pydantic-ai, the hybrid
retrieval primitives, the corpus + pluggable vector stores -- lives
in [`fireflyframework-agentic`](https://github.com/fireflyframework/fireflyframework-agentic).
flycanon's job is the composition: take raw bytes in any format,
ground them in a canonical version chain, and expose retrieval + RAG
over the result.

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
to `superseded`. The BM25 / FTS5 projection lives in a file-backed
SQLite corpus (`FLYCANON_CORPUS_PATH`) for portability; the dense
vector projection is fully pluggable -- see _Pluggable retrieval
backends_ below.

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

## Universal binary normaliser

`core/services/binary/normalizer.py` is the front door for every
inbound artefact. It detects the media type from the magic bytes
(stdlib `mimetypes` + a curated header table + ZIP central-directory
inspection to disambiguate Office formats from generic archives) and
routes the payload through a fixed matrix:

| Class           | Examples                                  | Strategy                                                                 |
|-----------------|-------------------------------------------|--------------------------------------------------------------------------|
| Plain text      | `text/plain`, `text/markdown`, `text/csv` | Pass-through -- decoded via charset detection.                           |
| PDF             | `application/pdf`                         | Encrypted / corrupt PDFs rejected with `unsupported_binary`; otherwise pass-through to MarkItDown. |
| Office          | DOCX / XLSX / PPTX / ODT / ODS / ODP / RTF | `office_converter=none` (default) feeds MarkItDown directly; `gotenberg` (HTTP sidecar) or `libreoffice` (in-container `soffice`) render to PDF first. |
| Raster images   | PNG / JPG / WEBP                          | Pass-through to `ImageLoader` (Tesseract OCR).                           |
| Converted images| HEIC / AVIF / TIFF / SVG / BMP            | Pillow + pillow-heif + cairosvg -> PNG, then OCR.                        |
| Archives        | ZIP / 7Z / TAR / TAR.GZ / TAR.BZ2         | Expanded recursively (capped at `binary_max_recursion_depth` and `binary_max_expanded_files`). Each child re-enters the normaliser. |
| Emails          | EML / MSG                                 | Body + each attachment exposed as a separate artefact carrying `parent_artifact` ancestry. |
| Web             | HTML / XHTML                              | MarkItDown.                                                              |
| Transcripts     | WebVTT / SRT                              | `TranscriptLoader` (cue-aware).                                          |
| Unknown         | _everything else_                         | `UnsupportedBinaryError` -> `IngestionFailed` event with stable `code`.  |

Multi-artefact intakes (archives, multi-attachment emails) are merged
into a single Markdown document with `## Artifact: <filename>`
section markers, so chunks remain attributable to their originating
artefact via `metadata.parent_artifact`.

## Pluggable retrieval backends

BM25 stays on a file-backed SQLite FTS5 index (portable, no extra
service, sufficient for the BM25 channel). The dense projection is
chosen at boot via `FLYCANON_VECTOR_STORE`:

| Value         | Use case                                                                 |
|---------------|--------------------------------------------------------------------------|
| `pgvector`    | **Default.** PostgreSQL + pgvector extension. HNSW index on `vector_cosine_ops`, tuneable `m` / `ef_construction`. Lives in the same operational Postgres as the canonical store. |
| `chroma`      | Self-hosted Chroma server. Namespaced by `FLYCANON_CHROMA_COLLECTION`.    |
| `qdrant`      | Self-hosted or Qdrant Cloud. `FLYCANON_QDRANT_URL` + optional API key.   |
| `pinecone`    | Pinecone Serverless. `FLYCANON_PINECONE_INDEX` + `FLYCANON_PINECONE_API_KEY`. |
| `sqlite-vec`  | Laptop / SBOM / single-process deployments. Same SQLite file as the FTS5 index. |
| `memory`      | Tests only -- evicted on process exit.                                   |

Switching backends is a config change -- the application code only
sees `VectorStoreProtocol`. Fusion always happens via Reciprocal
Rank Fusion (RRF) over the two channels.

## Layers

```
                        +-----------------------------------------+
                        |            web/controllers              |
                        |  rest_controller + DefaultCommand/Query |
                        |             advice/exception            |
                        +------------+----------------------------+
                                     | DefaultCommandBus / DefaultQueryBus
                                     v
                +----------------------------------------------------------+
                |                core/services/* handlers                  |
                |  @command_handler / @query_handler + frozen Command dt   |
                +------------+------------------------------------------+--+
                             |                                          |
                             v                                          v
        +------------------------------------+         +-----------------------------+
        |  service layer (no IO leaks here)  |         |  binary normaliser +        |
        |  KnowledgeService, AuditService,   |         |  loaders + intake +         |
        |  TaxonomyService, ProvenanceSvc    |         |  consolidation + retrieval  |
        +-------------+----------------------+         +-------------+---------------+
                      |                                              |
                      v                                              v
        +----------------------------+                   +--------------------------+
        |  models/repositories       |                   |  HybridRetriever:        |
        |  AsyncEngine + Repository  |                   |  - SQLite FTS5 (BM25)    |
        |  shared across the layer   |                   |  - pluggable vector store|
        +----------------------------+                   +--------------------------+
                      |                                              |
                      v                                              v
                Postgres (asyncpg)                            pgvector / chroma /
                                                              qdrant / pinecone /
                                                              sqlite-vec / memory
```

## DI wiring

[`core/configuration.py`](../src/flycanon/core/configuration.py) is
the **single** place outside the stereotype decorators where pyfly
beans are declared. It registers:

* settings (env-driven `CanonSettings` singleton)
* six repositories sharing one async engine
* `SqlAlchemyHealthIndicator` -> `/actuator/health`
* the binary normaliser stack (sniffer, PDF guard, image normaliser,
  archive unpacker, email unpacker, the chosen `OfficeConverter`)
* the universal loader registry (MarkItDown for Office / HTML, image
  loader with Tesseract OCR, transcript loader, plain-text loader)
* `EmbeddingService` (provider switch via `FLYCANON_EMBEDDING_MODEL`)
* `CorpusContext` + the chosen `VectorStoreProtocol` + `IndexService`
  + `RetrievalService`
* `AuditService`, `KnowledgeService`, `ProvenanceService`,
  `TaxonomyService` (each takes the `EventPublisher` so events are
  published as the canonical store mutates)
* `Consolidator` + `CandidateService`
* `SearchService` + `AnswerService`
* `IntakeService` (the end-to-end orchestrator)

`EventPublisher` is injected upstream by pyfly's
`EdaAutoConfiguration` (Postgres outbox by default; flip
`FLYCANON_EDA_ADAPTER` to memory / redis / kafka).

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
