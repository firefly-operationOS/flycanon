<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Architecture**

</div>

---

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
canon_workspaces        canonical store for workspace identity + lifecycle
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
to `superseded`. The BM25 projection is Postgres-native by default
(a `tsvector` GENERATED column on `canon_chunks.content` with a GIN
index -- see migration `0003_bm25_tsv`) so both retrieval channels
share the same operational Postgres; the file-backed SQLite FTS5
corpus is only used when the `sqlite-vec` vector backend is selected.
The dense vector projection is fully pluggable -- see _Pluggable
retrieval backends_ below.

Every `canon_*` row also carries `(tenant_id, workspace_id)` as the
multitenancy scope -- see _Workspace + multitenancy_ below.

## Workspace + multitenancy (Plan 2 foundation)

Flycanon is the **canonical store** for workspace identity (per the
unification spec section 4.1). The `canon_workspaces` table holds
the authoritative row for every workspace; sibling services
(flyradar, flydesk-*) read workspace details via a cached client
(added in their respective plans).

Every `canon_*` row carries `(tenant_id, workspace_id)` as NOT NULL
columns. Today they default to `'default'` on every insert -- Plan 4
(conventions adoption) will wire `require_tenant_context()` so
services pass real values from request headers. Composite
`(tenant_id, workspace_id)` indexes back the workspace-scoped query
paths that Plan 4 introduces.

### Workspace lifecycle

`WorkspaceStatus` is one of: `draft`, `active`, `closed`,
`handed_off`. The CRUD API at `/api/v1/workspaces` (added in Plan 4)
exposes:

- `POST /api/v1/workspaces` -- create
- `GET /api/v1/workspaces` -- list (tenant-scoped via header)
- `GET /api/v1/workspaces/{id}` -- fetch one
- `PATCH /api/v1/workspaces/{id}` -- update
- `POST /api/v1/workspaces/{id}:close` -- close (sets status +
  `closed_at`)

### Conventions module

`flycanon.web.conventions` (ported from flyradar in Plan 1) supplies
the building blocks for tenant-scoped HTTP: RFC 7807 envelope, the
`require_tenant_context()` FastAPI dependency, an exception
hierarchy that maps to RFC 7807, idempotency primitives, and a
tenant-safe outbound HTTP client. It is in-tree and unit-tested
today; controllers start consuming it in Plan 4.

## Embeddings: tenant + workspace isolation

flycanon's retrieval path is tenant-isolated end-to-end (Plan 3).
The two corpora -- BM25 over `canon_chunks` and dense vectors
over `canon_chunk_vectors` -- both filter `(tenant_id,
workspace_id)` before computing relevance.

### BM25 (PostgresCorpus)

The `tsvector` GIN index stays global. Queries add a
`WHERE tenant_id = ? AND workspace_id = ?` predicate; Postgres
intersects with the composite btree `ix_canon_chunks_scope_source`
cheaply. The composite index name + columns are pinned by
migration `0009_embeddings_scope`.

### Dense vectors (PgVectorVectorStore)

The HNSW index stays global; queries:

1. `SET LOCAL hnsw.ef_search = 200` (bumped from 40) so the
   candidate set is wide enough to filter without losing top-k.
2. `WHERE tenant_id = ? AND workspace_id = ?`.
3. `LIMIT k * widening_factor` (typically 5x), then trim to `k`
   client-side after re-rank.

For tenants that outgrow the global HNSW (~500k+ chunks), the
Tier-B partition-by-tenant escape valve exists in
`flycanon.core.services.retrieval.partition_admin`. DORMANT BY
DEFAULT; see the module docstring for the admin-triggered
roll-out (one-time partitioning migration + per-tenant
`promote_tenant_to_partition()`).

### RetrievalService scope threading

`RetrievalService.search()` requires `tenant_id` + `workspace_id`
kwargs and **fails closed** (raises `MissingTenantContext`) when
either is missing. Internally, the service wraps the two corpora
in scope-bound proxies (`_ScopedCorpus`, `_ScopedVectorStore`)
that inject the scope before delegating to the agentic
`HybridRetriever`. The framework's RRF math and OpenTelemetry
spans are untouched.

The write path (`IndexService.replace_for_source()`) accepts
optional scope kwargs with `'default'` fallback (column-level
defaults in Plan 2's migration catch forgotten writes). Plan 4
tightens both paths.

### Re-embed drift detection

The `ix_canon_chunks_tenant_workspace_model` index lets the
re-embed job detect `(tenant, workspace) x embedding_model`
drift -- the right composite when a model upgrade rolls out
per-workspace.

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
| PDF -- digital text | Born-digital PDFs (Word / LibreOffice / LaTeX exports, browser "Save as PDF", reporting output) | **Phase 1 (PyMuPDF text-layer):** `pymupdf.get_text()` per page returns the encoded text stream in reading order. No rendering, microseconds per page. |
| PDF -- image / scanned | Scanned contracts, photographed pages, fax output, mobile camera captures (raster pages, no encoded text) | **Phase 2 (OCR fallback):** pages under `_MIN_CHARS_PER_PAGE` rasterised by PyMuPDF at `_OCR_DPI` and piped through Tesseract (`pytesseract.image_to_string`) or Docling (with `FLYCANON_PDF_OCR_ENGINE=docling`). Composes with Phase 1 page-by-page for hybrid PDFs. |
| PDF -- guard rail | Encrypted or corrupt PDFs | Rejected up-front by `PdfGuard` (lightweight `pypdf` pre-flight) with `error_code=encrypted_pdf` / `corrupt_source`. |
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

The BM25 corpus is co-located with the dense projection so hybrid
retrieval is a single-host operation in the default deployment:

* `FLYCANON_VECTOR_STORE=pgvector` (default) -- BM25 rides on a
  `tsvector` + GIN index on `canon_chunks.tsv` (a Postgres GENERATED
  column derived from `content`; see migration `0003_bm25_tsv`). No
  extra service, no SQLite file -- both projections live in the same
  operational Postgres. The text-search config defaults to `simple`
  (multilingual, no stemming); flip
  `FLYCANON_BM25_TEXT_SEARCH_CONFIG` to `english` / `spanish` / &hellip;
  for language-aware stemming.
* Anything else (`sqlite-vec`, `chroma`, `qdrant`, `pinecone`,
  `memory`) -- BM25 falls back to the file-backed SQLite FTS5 corpus
  shipped by `fireflyframework-agentic`.

The dense projection is chosen at boot via `FLYCANON_VECTOR_STORE`:

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
        |  AsyncEngine + Repository  |                   |  - Postgres tsv+GIN BM25 |
        |  shared across the layer   |                   |    (file-backed FTS5     |
        |                            |                   |    only for sqlite-vec)  |
        |                            |                   |  - pluggable vector store|
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

## Conventions adoption (Plan 4)

All `/api/v1/*` requests except `/api/v1/version` require:

- `X-Tenant-Id` header (slug grammar: `^[a-z0-9][a-z0-9_-]{0,63}$`)
- `X-Workspace-Id` header (same grammar)

Missing -> `400 missing_tenant_context`. The
`require_tenant_context()` FastAPI dependency from
`flycanon.web.conventions` is wired into every controller; the
resolved `TenantContext` flows down through service signatures into
repositories and the retrieval layer.

### Error envelope

flycanon now emits the RFC 7807 envelope from
`flycanon.web.conventions.ProblemDetail`:

```json
{
  "type": "https://firefly.dev/problems/<code>",
  "code": "<snake_case_machine_code>",
  "title": "<human-readable label>",
  "status": <int>,
  "detail": "<message>",
  "instance": "<path>",
  "correlation_id": "<uuid>",
  "errors": []
}
```

Media type `application/problem+json`. Programmatic clients dispatch
on `code` (stable slug); `title` and `detail` are translation-friendly
but not contract. The legacy `flycanon.interfaces.dtos.error.ProblemDetails`
(plural) and `flycanon.web.problem_handlers` are deleted -- the
`flycanon.web.conventions.register_exception_handlers()` registrar
installs handlers at boot.

### Workspace CRUD

`canon_workspaces` (Plan 2) is the canonical workspace store. CRUD
lives at `/api/v1/workspaces`:

- `POST /api/v1/workspaces` -- create
- `GET /api/v1/workspaces` -- list within tenant
- `GET /api/v1/workspaces/{id}` -- fetch
- `PATCH /api/v1/workspaces/{id}` -- update (sparse)
- `POST /api/v1/workspaces/{id}:close` -- close (idempotent;
  sets `status=closed` + `closed_at`)

All five endpoints require the standard headers. The lifecycle
states (`draft` / `active` / `closed` / `handed_off`) match the
`WorkspaceStatus` enum from Plan 2.

### `actor` retirement

`actor` is no longer used for partitioning. Tenant + workspace come
from headers via `TenantContext`. `actor` remains as audit metadata
on rows (who/what created the row) -- `ctx.actor` is the canonical
source; the field stays on `canon_audit_events` and
`canon_cost_events` as a free-form caller identifier. Billing
endpoints no longer accept an `actor` Query param; aggregations
group by `(tenant_id, workspace_id)` instead.

### `/api/v1/jobs/*` -> `/api/v1/ingest-jobs/*`

The ingest-job inspection routes are renamed for clarity (parallel
to flyradar's `/api/v1/discovery-jobs/*`). Three sub-routes moved:

- `GET /api/v1/jobs` -> `GET /api/v1/ingest-jobs`
- `GET /api/v1/jobs/{id}` -> `GET /api/v1/ingest-jobs/{id}`
- `GET /api/v1/jobs/{id}/stream` -> `GET /api/v1/ingest-jobs/{id}/stream`

The async-enqueue path (`POST /api/v1/sources?mode=async`) is
unchanged.

### RetrievalService scope threading (operational again)

`RetrievalService.search()` requires `tenant_id` + `workspace_id`
from `TenantContext`. The `/api/v1/search`, `/api/v1/query`, and
`/api/v1/query/stream` endpoints -- which Plan 3 left fail-closed
because their callers didn't pass scope -- are now operational
again. Scope flows from request headers through `SearchService` /
`AnswerService` into the retrieval layer.

### Service-layer scope + widened constraints

Services no longer rely on the `'default'` column-level
`server_default` for `tenant_id` + `workspace_id`. Migration `0011`
drops those defaults and widens the
`canon_sources.content_sha256` unique constraint to
`(tenant_id, workspace_id, content_sha256)` -- the same content
can now coexist in multiple workspaces and dedup is workspace-local.
