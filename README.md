<div align="center">

<img src="docs/assets/logo.png" alt="flycanon — operational knowledge repository" width="520" />

### **Operational Knowledge Repository**

The living source of truth for canonical operational knowledge.
Universal ingestion, hybrid retrieval, retrieval-augmented answering
with citations — all behind a single HTTP service.

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org)
[![Java 25](https://img.shields.io/badge/java%20sdk-25%20%2B%20Spring%20Boot%203.5.9-orange)](sdks/java/README.md)
[![pyfly](https://img.shields.io/badge/runtime-fireflyframework--pyfly-orange)](https://github.com/fireflyframework/fireflyframework-pyfly)
[![agentic](https://img.shields.io/badge/genai-fireflyframework--agentic-purple)](https://github.com/fireflyframework/fireflyframework-agentic)
[![OpenAPI](https://img.shields.io/badge/api-openapi%203.1-green)](docs/api-reference.md)
[![pgvector](https://img.shields.io/badge/default--vector--store-pgvector-336791)](docs/architecture.md#pluggable-retrieval-backends)
[![Version](https://img.shields.io/badge/version-26.5.6-green.svg)](#)
[![License](https://img.shields.io/badge/license-Proprietary-lightgrey.svg)](LICENSE)

</div>

---

> **In a hurry?** &nbsp;Jump to the [**10-minute Quickstart →**](QUICKSTART.md) &nbsp;·&nbsp; SDK paths: [Python](sdks/python/QUICKSTART.md) · [Java / Spring Boot](sdks/java/QUICKSTART.md) &nbsp;·&nbsp; Wire payloads: [**Payload reference →**](docs/payload-reference.md)

---

## Why this service exists

Every operations team builds the same workflow underneath process
docs, compliance runbooks, vendor agreements, internal policies, and
post-incident retrospectives:

> _"Take this document, file it under the right thing, keep the
> version chain honest, and let me ask it questions later — with
> citations I can audit."_

Doing that with a wiki is a losing game: layouts change, formats
mutate (DOCX, PDF, scanned PDF, HEIC, ZIP bundles, `.eml`
threads…), and the team ends up hand-copying snippets into another
tool every time a single thing moves.

**flycanon** collapses the whole workflow into a single HTTP
service. You ship any file format, declare the metadata you care
about, and the service hands back a structured `SourceRecord` whose
content is parsed, normalised, chunked, embedded, and indexed —
ready for hybrid retrieval and grounded RAG answers. Knowledge is
never edited in place: every revision appends a new version row, the
previous one transitions to `superseded`, and the provenance graph
travels with it.

It is built to drop into a production back-office stack: idempotent
APIs, event-driven downstream notifications via a durable Postgres
outbox, observability out of the box, and clean failure isolation
per pipeline stage.

---

## What you get back

You give the service one HTTP request. The response is a single JSON
object that carries, for every interaction:

| Layer                       | What it tells you                                                                                                                              |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **Sources**                 | `SourceRecord` per ingested artefact (id, kind, status, content sha256, chunk count, ancestry chain when the artefact came out of a bundle). Bulk + async ingest variants return per-item results / a job id; `PUT /api/v1/sources/{id}` re-ingests in place and preserves the row id. |
| **Knowledge items**         | Canonical pointer (status, current version, domain, jurisdiction). Updates append a new version; the previous one flips to `superseded`. `GET /api/v1/knowledge/{id}/diff` returns a unified diff + field changes + citation set deltas between any two versions. |
| **Knowledge versions**      | Append-only revisions of a knowledge item. Citations to source chunks travel with the edge.                                                    |
| **Knowledge graph**         | Typed edges between items (`related` / `depends_on` / `conflicts_with` / `replaces`) over `/api/v1/knowledge/{id}/relations`, plus a whole-canon view at `/api/v1/knowledge:graph` (JSON or `Accept: text/vnd.mermaid`). Conflict detection materialises `conflicts_with` edges automatically. |
| **Candidates**              | Pre-canonical LLM proposals tied to a source. Accept / reject lifecycle materialises them into the knowledge chain.                            |
| **Hybrid retrieval**        | `SearchResponse` with BM25 (Postgres `tsvector` + GIN by default; SQLite FTS5 when the vector backend lives elsewhere) + dense vectors fused via Reciprocal Rank Fusion (RRF), optional cross-encoder rerank (Cohere / Voyage), and optional LLM query expansion. Each hit carries `chunk_id`, `source_id`, `source_filename`, `source_title`, `source_kind`, `source_uri`, `section_path`, `page`, the matching `content`, and the fused `score` — UIs can render citation labels without a second `GET /api/v1/sources/{id}`. |
| **Grounded RAG answers**    | `AnswerResponse` with the answer + citation list (same enriched `Hit` shape — filename / title / kind / section / page populated), `model`, `elapsed_ms`. `POST /api/v1/query:stream` emits the same payload as Server-Sent Events. A grounded "I don't know" is `answer == ""` with empty citations — flycanon never hallucinates. |
| **Conversations**           | Multi-turn threads at `/api/v1/conversations/...` with rolling summary + last-N-turn context windowing. Each turn returns the same enriched citation set as `/query`; `:suggest` proposes 3-5 grounded follow-up questions. |
| **Provenance**              | Resolved citation graph for one knowledge version plus the source summaries it touches plus the version chain of its item.                     |
| **Async ingest jobs**       | `IngestJob` row + SSE event stream for any large or bulk ingest. Status / stage / progress / source id / RFC 7807 error envelope all surface through `GET /api/v1/ingest-jobs/{id}` and `GET /api/v1/ingest-jobs/{id}/stream` (cursor-resumable). |
| **Knowledge quality**       | `GET /api/v1/knowledge:stale` returns per-item staleness scores (cosine vs fresh sources, 6h cached); `POST /api/v1/knowledge:detect-conflicts` runs an LLM-judged pairwise conflict scan, queues confirmed conflicts as candidates, and auto-creates the matching `conflicts_with` edges. |
| **PII guardrail**           | Configurable regex scanner with four policies (`disabled` / `warn` / `redact` / `reject`). Runs on every intake path (initial submit, bulk, async, replace). `reject` returns RFC 7807 + `findings[]` so callers can surface a precise diagnostic. |
| **Billing + cost stream**   | `/api/v1/billing` aggregates spend; `/events` drills into per-call breadcrumbs (correlation id, subject, latency); `/summary` returns 24h / 7d / 30d snapshots; `/top` and `/by-subject` answer "who" and "where did it go"; `/latency` returns p50 / p95 / p99 from the same cost-event stream. |
| **Corpus inventory**        | `GET /api/v1/stats` -- one-shot snapshot covering sources (by kind + status + bytes), knowledge items (by status + domain), versions, candidates, chunks (embedded coverage), ingest jobs (by status + avg attempts), and the cost headline (24h / 30d). |
| **Append-only audit log**   | Every mutation (`/api/v1/audit`) with correlation id, actor, payload, and W3C trace context.                                                   |
| **EDA topics**              | Three durable topics published via the Postgres outbox: `flycanon.ingest`, `flycanon.knowledge`, `flycanon.audit`.                              |
| **RFC 7807 error envelope** | Every non-2xx response is a `ProblemDetail` (singular) payload from `flycanon.web.conventions` with a stable `code` field for branching. `type` URI base: `https://firefly.dev/problems/...`. |
| **OpenAPI 3.1**             | Multi-paragraph DTO descriptions mixing business and technical context, served live at `/openapi.json` (Swagger UI at `/docs`, ReDoc at `/redoc`). |

---

## Universal ingestion

Submit any file format. flycanon detects the media type from the
magic bytes (stdlib `mimetypes` + a curated header table + ZIP
central-directory inspection to disambiguate Office formats from
generic archives) and routes the payload through a fixed routing
matrix before the parse / chunk / embed / index pipeline runs:

| Class            | Examples                                          | Strategy                                                                                                                                                          |
| ---------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Plain text       | `text/plain`, `text/markdown`, `text/csv`, JSON, XML | Pass-through.                                                                                                                                                     |
| **PDF — Full Digital Text** | Born-digital PDFs (Word / LibreOffice / LaTeX exports, browser "Save as PDF", reporting pipelines) | **Phase 1 (PyMuPDF text-layer):** `pymupdf.get_text()` per page returns the encoded text stream in reading order. No rendering, microseconds per page. |
| **PDF — Image (scanned)** | Scanned contracts, fax output, photographed pages, mobile-camera captures — pages are raster images of the original | **Phase 2 (OCR fallback):** pages under `_MIN_CHARS_PER_PAGE` rasterised by PyMuPDF at `_OCR_DPI` (200) and OCR'd via Tesseract (`pytesseract.image_to_string`) — engine selectable via `FLYCANON_PDF_OCR_ENGINE` (`tesseract` default; `docling` for layout-aware OCR with the `docling` extra). Languages default to `eng+spa`, override via `FLYCANON_OCR_LANG`. |
| **PDF — Hybrid** | Mixed: typed body + scanned signature page, or any blend of digital and image pages | Phase 1 runs on every page; Phase 2 only fires for pages flagged as image-only. The two phases compose page-by-page. |
| PDF — guard rail | Encrypted or corrupt PDFs                       | Rejected up-front by `PdfGuard` (lightweight `pypdf` pre-flight) with `error_code=encrypted_pdf` / `corrupt_source`.                                              |
| Office           | DOCX / XLSX / PPTX / ODT / ODS / ODP / RTF        | `office_converter=none` (default) feeds MarkItDown directly; `gotenberg` (HTTP sidecar) or `libreoffice` (in-container `soffice`) render to PDF first.            |
| Raster images    | PNG / JPG / WEBP                                  | Pass-through to OCR (Tesseract, multi-language).                                                                                                                  |
| Converted images | HEIC / AVIF / TIFF / SVG / BMP                    | Pillow + pillow-heif + cairosvg → PNG, then OCR.                                                                                                                  |
| Archives         | ZIP / 7Z / TAR / TAR.GZ / TAR.BZ2 / EPUB          | Expanded recursively (capped at `binary_max_recursion_depth` and `binary_max_expanded_files`). Each child re-enters the normaliser.                               |
| Emails           | EML / MSG                                         | Body + each attachment exposed as a separate artefact carrying `parent_artifact` ancestry.                                                                        |
| Web              | HTML / XHTML                                      | MarkItDown.                                                                                                                                                       |
| Transcripts      | WebVTT / SRT                                      | Cue-aware loader.                                                                                                                                                 |
| Unknown          | _everything else_                                 | `UnsupportedBinaryError` → `IngestionFailed` event with stable `code`.                                                                                            |

Multi-artefact intakes (archives, multi-attachment emails) are merged
into a single Markdown document with `## Artifact: <filename>`
section markers, so chunks remain attributable via
`metadata.parent_artifact`.

---

## Backend-agnostic retrieval

The BM25 corpus is co-located with the dense projection so hybrid
retrieval is single-host in the default deployment:

- **`pgvector` (default)** — BM25 rides on a `tsvector` + GIN index
  on `canon_chunks.tsv` (a Postgres GENERATED column derived from
  `content`). No extra service, no SQLite file — both projections
  live in the same operational Postgres. Text-search config is
  `simple` by default (multilingual); switch to `english` /
  `spanish` / … via `FLYCANON_BM25_TEXT_SEARCH_CONFIG`.
- **`sqlite-vec` / `chroma` / `qdrant` / `pinecone` / `memory`** —
  BM25 falls back to the file-backed SQLite FTS5 corpus shipped by
  `fireflyframework-agentic` (portable, single-file, no extra
  service).

The dense projection is chosen at boot via `FLYCANON_VECTOR_STORE`:

| Backend      | Use case                                                                                                                                |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| **`pgvector`**   | **Default.** PostgreSQL + pgvector extension. HNSW index on `vector_cosine_ops`, tuneable `m` / `ef_construction`. Same operational Postgres as the canonical store AND the BM25 projection. |
| `chroma`     | Self-hosted Chroma server. Namespaced by `FLYCANON_CHROMA_COLLECTION`.                                                                  |
| `qdrant`     | Self-hosted or Qdrant Cloud. `FLYCANON_QDRANT_URL` + optional API key.                                                                  |
| `pinecone`   | Pinecone Serverless. `FLYCANON_PINECONE_INDEX` + `FLYCANON_PINECONE_API_KEY`.                                                            |
| `sqlite-vec` | Laptop / single-process deployments. Same SQLite file as the FTS5 BM25 index.                                                           |
| `memory`     | Tests only — evicted on process exit.                                                                                                   |

Switching backends is a config change — the application code only
sees `VectorStoreProtocol`. Fusion always happens via Reciprocal
Rank Fusion over the two channels.

---

## Public surface

| Concern                                                          | Endpoint(s)                                |
| ---------------------------------------------------------------- | ------------------------------------------ |
| Source intake (any format, bytes / base64 / URL)                 | `POST /api/v1/sources`                     |
| Bulk + async intake (jobs + SSE progress)                        | `POST /api/v1/sources:bulk`, `:async`, `GET /api/v1/ingest-jobs/{id}/stream` |
| Source re-ingest (preserves the row id)                          | `PUT /api/v1/sources/{id}`                 |
| Source lookup / pagination                                       | `GET /api/v1/sources[/{id}]`               |
| Knowledge-item lifecycle (draft / published / superseded / retired) | `/api/v1/knowledge/...`                 |
| Versioned diff between two knowledge versions                    | `GET /api/v1/knowledge/{id}/diff`          |
| Knowledge graph (typed edges + JSON / Mermaid view)              | `/api/v1/knowledge/{id}/relations`, `GET /api/v1/knowledge:graph` |
| Hybrid retrieval (+ optional rerank + query expansion)           | `POST /api/v1/search`                      |
| RAG answer with citations (+ token streaming)                    | `POST /api/v1/query`, `POST /api/v1/query:stream` |
| Multi-turn conversations + suggested follow-ups                  | `/api/v1/conversations/...`                |
| Candidate proposals (pre-canonical)                              | `/api/v1/candidates/...`                   |
| Provenance graph                                                 | `GET /api/v1/knowledge/{id}/provenance`    |
| Quality scans (staleness + conflict detection)                   | `GET /api/v1/knowledge:stale`, `POST /api/v1/knowledge:detect-conflicts` |
| Cost / billing rollups                                           | `GET /api/v1/billing` (aggregate)          |
| Cost drill-down -- per-call events                               | `GET /api/v1/billing/events`               |
| Cost drill-down -- 24h / 7d / 30d snapshot                       | `GET /api/v1/billing/summary`              |
| Cost drill-down -- top-N consumers                               | `GET /api/v1/billing/top`                  |
| Cost drill-down -- per-subject attribution                       | `GET /api/v1/billing/by-subject`           |
| Cost drill-down -- latency percentiles (p50/p95/p99)             | `GET /api/v1/billing/latency`              |
| Corpus + queue + cost inventory snapshot                         | `GET /api/v1/stats`                        |
| Append-only audit log                                            | `GET /api/v1/audit`                        |
| Taxonomy (domain + jurisdiction)                                 | `/api/v1/taxonomy/...`                     |
| Agent-token CRUD (user-tier; mint returns secret ONCE)           | `/api/v1/agent-tokens`                     |
| Agent surface (`X-Agent-Token`-protected, 8 endpoints)           | `/api/v1/agent/sources`, `.../query`, `.../query/stream`, `.../search`, `.../knowledge/{id}`, `.../knowledge/{id}/provenance`, `.../candidates:propose` |
| Identity / model info                                            | `GET /api/v1/version`                      |
| Health / readiness / liveness                                    | `/actuator/health/...`                     |
| OpenAPI 3.1                                                      | `/openapi.json`, `/docs`, `/redoc`         |

---

## Quickstart

> **Want the 10-minute curl tour instead?** &nbsp;See [`QUICKSTART.md`](QUICKSTART.md)
> — `task docker:up:test` + one curl call against a mock LLM, no API keys.

```bash
git clone https://github.com/firefly-operationOS/flycanon.git
cd flycanon
task deps:install          # uv sync --extra dev (pins .venv)
task docker:up             # api + worker + postgres(pgvector) + redis
curl -fsS http://localhost:8500/actuator/health | jq .
```

Ingest a sample DOCX (the binary normaliser handles every format —
this is just the simplest curl):

```bash
curl -fsS -X POST http://localhost:8500/api/v1/sources \
  -F "file=@./tests/fixtures/sample.docx" \
  -F 'metadata={"title":"Sample","domain":"process_owner"};type=application/json' \
  | jq .
```

Search the corpus:

```bash
curl -fsS -X POST http://localhost:8500/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"what does the document say about scope","top_k":5}' | jq .
```

Ask a grounded question:

```bash
curl -fsS -X POST http://localhost:8500/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Summarise the scope section in three sentences."}' | jq .
```

A grounded "I don't know" looks like `{"answer":"","citations":[]}`
— flycanon never hallucinates.

---

## Local development

```bash
task dev:db              # Postgres (pgvector/pg16) + Redis only
task dev:migrate         # alembic upgrade head
task dev:serve           # FastAPI hot-reload on :8500
task dev:worker          # EDA worker in a separate terminal
```

Smoke the running service:

```bash
task health              # /actuator/health
task version             # /api/v1/version
task openapi             # /openapi.json
```

---

## SDKs

Both SDKs pin their version to the service's CalVer (`26.5.6`), so
the client and server upgrade in lockstep.

| SDK | Highlights |
|-----|------------|
| [**Python**](sdks/python/README.md) | Async-first, `httpx` + Pydantic. Python ≥ 3.11. |
| [**Java**](sdks/java/README.md)     | **Spring Boot 3.5.9 + Spring `RestClient` + Jackson. Java 25 (LTS). `groupId = com.firefly`.** Ships an `@AutoConfiguration` so a `CanonClient` bean is wired straight from `flycanon.*` properties. |

Java consumers just declare the dependency and inject the bean:

```java
@Service
public class CopilotService {
    private final CanonClient canon;
    public CopilotService(CanonClient canon) { this.canon = canon; }
    // canon.submitSource(...), canon.search(...), canon.answer(...)
}
```

---

## Documentation

| Document | Read it when… |
|----------|---------------|
| [QUICKSTART.md](QUICKSTART.md) | You want your first ingest + search + answer in ten minutes (HTTP / curl). |
| [docs/architecture.md](docs/architecture.md) | You need the data model, the binary-normaliser routing matrix, the pluggable retrieval backend matrix, the dependency arrows. |
| [docs/pipeline.md](docs/pipeline.md) | You're touching the orchestrator, adding a new stage, or chasing a slow ingest. |
| [docs/api-reference.md](docs/api-reference.md) | You're integrating with the HTTP API and need every endpoint, shape, and status code. |
| [docs/payload-reference.md](docs/payload-reference.md) | You're composing the request payload — every field, option, and example. |
| [docs/eda-events.md](docs/eda-events.md) | You're subscribing to the `flycanon.ingest` / `flycanon.knowledge` / `flycanon.audit` / `canon.workspaces.v1` topics. |
| [docs/consumers.md](docs/consumers.md) | You're building or auditing a service that consumes flycanon -- agent token scopes, workspace events, retry posture, wire-contract stability. |
| [docs/deployment.md](docs/deployment.md) | You're running this in production — env vars, topologies, OCR engines, embedding providers, auth, observability, sizing. |
| [docs/cicd.md](docs/cicd.md) | You're cutting a release or wiring CI/CD — the three GitHub Actions workflows, release cookbook, required secrets. |
| [docs/troubleshooting.md](docs/troubleshooting.md) | The service / ingest / search / answer surface is misbehaving — symptom → root cause → fix. |
| [docs/glossary.md](docs/glossary.md) | You need a precise definition for a term the API or docs use. |
| [sdks/python/README.md](sdks/python/README.md) | You're integrating from Python — async-first SDK with Pydantic typing. |
| [sdks/java/README.md](sdks/java/README.md) | You're integrating from Java / Spring Boot — Spring Boot 3.5.9, `com.firefly` groupId, `@AutoConfiguration`. |

The OpenAPI 3.1 document is served live by the running service at
`/openapi.json`, with Swagger UI at `/docs` and Redoc at `/redoc`.

---

## Repository layout

```
flycanon/
├─ Dockerfile                # Multi-stage build with the binary-normaliser system deps
├─ Taskfile.yml              # Canonical dev-loop interface
├─ docker-compose.yml        # api + worker + postgres (pgvector) + redis (optional gotenberg)
├─ docker-compose.test.yml   # Adds the mock LLM for integration tests
├─ pyfly.yaml                # pyfly application configuration
├─ alembic.ini               # Migration runner config
├─ env_template              # Reference environment file (.env is gitignored)
├─ migrations/               # Alembic versions
├─ src/flycanon/
│  ├─ app.py                 # @pyfly_application + scan_packages
│  ├─ main.py                # ASGI entry consumed by uvicorn
│  ├─ cli.py                 # `flycanon {serve,worker,migrate}`
│  ├─ config.py              # CanonSettings (FLYCANON_* env)
│  ├─ core/                  # @configuration + services + binary normaliser + mappers
│  ├─ interfaces/            # Public DTOs + enums
│  ├─ models/                # SQLAlchemy entities + repositories
│  ├─ resources/prompts/     # YAML prompt templates
│  └─ web/                   # @rest_controller + @controller_advice
├─ sdks/
│  ├─ python/                # Async-first Python SDK (Apache-2.0)
│  └─ java/                  # Spring Boot Java SDK (Apache-2.0, com.firefly)
├─ docs/                     # Architecture, payload reference, API reference, EDA events, glossary
└─ tests/
   ├─ unit/
   └─ integration/
```

---

## License

The service is proprietary — see [`LICENSE`](LICENSE).

The SDKs under [`sdks/python`](sdks/python) and [`sdks/java`](sdks/java)
are released under the Apache License 2.0; each ships its own LICENSE
file.

---

<div align="center">

Part of **[Firefly OperationOS](https://github.com/firefly-operationOS)**. &nbsp;Platform-agnostic by design.

</div>
