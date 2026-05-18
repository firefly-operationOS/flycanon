<p align="center">
  <img src="docs/assets/logo.png" alt="flycanon" width="520" />
</p>

<p align="center">
  <em>Operational Knowledge Repository &mdash; the living source of truth for canonical operational knowledge.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Proprietary-lightgrey.svg"></a>
  <a href="#"><img alt="Python" src="https://img.shields.io/badge/python-3.13-blue.svg"></a>
  <a href="#"><img alt="Java SDK" src="https://img.shields.io/badge/sdk--java-25%20%2B%20Spring%20Boot%203.5.9-orange.svg"></a>
  <a href="#"><img alt="Version" src="https://img.shields.io/badge/version-26.5.1-green.svg"></a>
  <a href="docs/architecture.md"><img alt="Docs" src="https://img.shields.io/badge/docs-architecture-blueviolet.svg"></a>
</p>

---

**flycanon** is a standalone HTTP microservice that owns the data plane
for an organisation's canonical knowledge: ingestion, versioning,
provenance, hybrid retrieval, and retrieval-augmented answering. Other
services in Firefly OperationOS talk to it over REST or subscribe to
its EDA topics; flycanon ships none of the workflow UI, none of the
back-office screens, and none of the active-monitoring surfaces &mdash;
only the rich, agentic intelligence layer.

## Highlights

- **Universal ingestion.** Submit any file format -- DOCX, XLSX, PPTX,
  PDF, RTF, ODF, HTML, Markdown, plain text, CSV, JSON, XML, EPUB,
  emails (`.eml` / `.msg`), images (PNG / JPG / HEIC / AVIF / TIFF /
  SVG), archives (`.zip` / `.7z` / `.tar.gz`), transcripts
  (`.vtt` / `.srt`), or just a URL. flycanon detects the media type
  from the magic bytes, normalises through a routing matrix
  (Office &rarr; Markdown, archives expanded recursively, images
  OCR'd, emails decomposed into body + attachments, &hellip;) and feeds
  the result into the canonical pipeline.

- **Backend-agnostic retrieval.** Pluggable vector store: PostgreSQL
  &#43; **pgvector** (default), Chroma, Qdrant, Pinecone, sqlite-vec
  for laptops, or an in-memory store for tests. BM25 stays on a
  file-backed SQLite FTS5 index for portability. Fusion via
  Reciprocal Rank Fusion (RRF).

- **Retrieval-augmented answers, fully cited.** Every answer carries
  citations to the underlying chunks. No-answer pathways are explicit
  (`answer == ""` with an empty citation list) -- never hallucinated.

- **Event-driven by default.** Three durable topics published via the
  Postgres outbox: `flycanon.ingest` (intake lifecycle),
  `flycanon.knowledge` (lifecycle of canonical items),
  `flycanon.audit` (mirror of every audited mutation).

- **Production-grade DX.** Spring-Boot-style autoconfig for the Java
  SDK, async-first Python SDK, rich OpenAPI (multi-paragraph DTO
  descriptions, mixed business + technical context), RFC 7807
  ProblemDetails with stable `code` field, append-only audit log,
  CalVer versioning (`26.5.1`).

## Stack

flycanon is built on the Firefly Framework:

- [`fireflyframework-pyfly`](https://github.com/fireflyframework/fireflyframework-pyfly)
  -- DI (`@service` / `@bean` / `@configuration`), CQRS
  (`@command_handler` / `@query_handler`), EDA, web (Starlette /
  FastAPI), observability, resilience, actuator.
- [`fireflyframework-agentic`](https://github.com/fireflyframework/fireflyframework-agentic)
  -- FireflyAgent over pydantic-ai, MarkItDown intake, hybrid
  retrieval (BM25 + vector + RRF fusion), embedding-provider
  abstraction.

## Public surface

| Concern                                                          | Endpoint(s)                          |
|------------------------------------------------------------------|--------------------------------------|
| Source intake (any format, see _Universal ingestion_ above)      | `POST /api/v1/sources`               |
| Source lookup / pagination                                       | `GET /api/v1/sources[/{id}]`         |
| Knowledge-item lifecycle (draft / published / superseded / retired) | `/api/v1/knowledge/...`           |
| Hybrid retrieval (BM25 + vectors, RRF fusion)                    | `POST /api/v1/search`                |
| RAG answer with citations                                        | `POST /api/v1/query`                 |
| Candidate proposals (pre-canonical)                              | `/api/v1/candidates/...`             |
| Provenance graph                                                 | `GET /api/v1/knowledge/{id}/provenance` |
| Append-only audit log                                            | `GET /api/v1/audit`                  |
| Taxonomy (domain + jurisdiction)                                 | `/api/v1/taxonomy/...`               |
| Identity / model info                                            | `GET /api/v1/version`                |
| Health / readiness / liveness                                    | `/actuator/health/...`               |
| OpenAPI                                                          | `/openapi.json`, `/docs`, `/redoc`   |

## EDA topics published

| Topic                  | Events                                                                 |
|------------------------|------------------------------------------------------------------------|
| `flycanon.ingest`      | `SourceIngested`, `IngestionFailed`                                    |
| `flycanon.knowledge`   | `KnowledgeItemPublished`, `KnowledgeItemSuperseded`, `KnowledgeItemRetired` |
| `flycanon.audit`       | Mirror of every audited mutation (sized for compliance projections)    |

## Quickstart

```bash
task deps:install       # uv sync --extra dev
task docker:up          # full stack: api + worker + postgres (pgvector) + redis
curl -fsS http://localhost:8500/actuator/health | jq .
```

The full five-minute tour is in [`QUICKSTART.md`](QUICKSTART.md);
architecture, payload reference, and the API catalogue live under
[`docs/`](docs/).

## Local development

```bash
task dev:db             # Postgres (pgvector/pg16) + Redis only
task dev:migrate        # alembic upgrade head
task dev:serve          # FastAPI hot-reload on :8500
task dev:worker         # EDA worker in a separate terminal
```

Smoke the running service:

```bash
task health             # /actuator/health
task version            # /api/v1/version
task openapi            # /openapi.json
```

## SDKs

Both SDKs pin their version to the service's CalVer (`26.5.1`), so the
client and server upgrade in lockstep.

| SDK | Highlights |
|-----|------------|
| [Python](sdks/python/README.md) | Async-first, `httpx` + Pydantic. Python &ge; 3.11. |
| [Java](sdks/java/README.md)     | **Spring Boot 3.5.9 + Spring `RestClient` + Jackson. Java 25 (LTS). `groupId = com.firefly`.** Ships an `@AutoConfiguration` so a `CanonClient` bean is wired straight from `flycanon.*` properties. |

Java consumers just declare the dependency and inject the bean:

```java
@Service
public class CopilotService {
    private final CanonClient canon;
    public CopilotService(CanonClient canon) { this.canon = canon; }
    // ... use canon.submitSource(...), canon.search(...), canon.answer(...)
}
```

## Repository layout

```
flycanon/
+- Dockerfile                # Multi-stage build with the binary-normaliser system deps
+- Taskfile.yml              # Canonical dev-loop interface
+- docker-compose.yml        # api + worker + postgres (pgvector) + redis
+- docker-compose.test.yml   # Adds the mock LLM for integration tests
+- pyfly.yaml                # pyfly application configuration
+- alembic.ini               # Migration runner config
+- env_template              # Reference environment file (.env is gitignored)
+- migrations/               # Alembic versions
+- src/flycanon/
|  +- app.py                 # @pyfly_application + scan_packages
|  +- main.py                # ASGI entry consumed by uvicorn
|  +- cli.py                 # `flycanon {serve,worker,migrate}`
|  +- config.py              # CanonSettings (FLYCANON_* env)
|  +- core/                  # @configuration + services + binary normaliser + mappers
|  +- interfaces/            # Public DTOs + enums
|  +- models/                # SQLAlchemy entities + repositories
|  +- resources/prompts/     # YAML prompt templates
|  +- web/                   # @rest_controller + @controller_advice
+- sdks/
|  +- python/                # Async-first Python SDK (Apache-2.0)
|  +- java/                  # Spring Boot Java SDK (Apache-2.0, com.firefly)
+- docs/                     # Architecture, payload reference, API reference, EDA events, glossary
+- tests/
   +- unit/
   +- integration/
```

## License

The service is proprietary -- see [`LICENSE`](LICENSE).

The SDKs under [`sdks/python`](sdks/python) and [`sdks/java`](sdks/java)
are released under the Apache License 2.0; each ships its own LICENSE
file.

---

<p align="center">
  Part of <a href="https://github.com/firefly-operationOS"><strong>Firefly OperationOS</strong></a>. Platform-agnostic by design.
</p>
