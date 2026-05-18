# flycanon

**Operational Knowledge Repository -- the living source of truth for
operational canonical knowledge.** flycanon is a standalone HTTP
microservice that owns the data plane for an organisation's canonical
knowledge: ingestion, versioning, provenance, hybrid retrieval, and
retrieval-augmented answering. Other services in Firefly OperationOS
talk to it over REST or subscribe to its EDA topics; flycanon ships
none of the workflow UI, none of the back-office screens, and none of
the active-monitoring surfaces -- only the rich, agentic intelligence
layer.

The service is built on:

- [`fireflyframework-pyfly`](https://github.com/fireflyframework/fireflyframework-pyfly)
  -- DI, CQRS, EDA, web (Starlette/FastAPI), observability, resilience,
  actuator.
- [`fireflyframework-agentic`](https://github.com/fireflyframework/fireflyframework-agentic)
  -- FireflyAgent over pydantic-ai, MarkItDown intake, hybrid
  retrieval (BM25 + vector + RRF fusion), embedding-provider abstraction.

Part of Firefly OperationOS. Platform-agnostic by design.

## What it owns

| Concern | Endpoint(s) |
|---------|-------------|
| Source intake (DOCX / PDF / HTML / Markdown / TXT) | `POST /api/v1/sources` |
| Knowledge-item lifecycle (draft / published / superseded / retired) | `/api/v1/knowledge/...` |
| Hybrid retrieval (BM25 + vectors) | `POST /api/v1/search` |
| RAG answer with citations | `POST /api/v1/query` |
| Candidate proposals (pre-canonical) | `/api/v1/candidates/...` |
| Provenance graph | `GET /api/v1/knowledge/{id}/provenance` |
| Append-only audit log | `GET /api/v1/audit` |
| Taxonomy (domain + jurisdiction) | `/api/v1/taxonomy/...` |
| Identity / model info | `GET /api/v1/version` |
| Health / readiness / liveness | `/actuator/health/...` |

EDA topics published downstream:

- `flycanon.knowledge` -- lifecycle events (`KnowledgeItemPublished`,
  `KnowledgeItemSuperseded`, `KnowledgeItemRetired`).
- `flycanon.ingest` -- source-intake events
  (`SourceIngested`, `IngestionFailed`).
- `flycanon.audit` -- mirror of every audited mutation, sized for
  downstream compliance projections.

## Quickstart

```bash
task deps:install       # uv sync --extra dev
task docker:up          # full stack: api + worker + postgres + redis
curl -fsS http://localhost:8500/actuator/health | jq .
```

See [`QUICKSTART.md`](QUICKSTART.md) for the five-minute tour, and
[`docs/`](docs/) for architecture, payload reference, and API reference.

## Local development

```bash
task dev:db             # Postgres + Redis only
task dev:migrate        # alembic upgrade head
task dev:serve          # FastAPI hot-reload on :8500
task dev:worker         # EDA worker in a separate terminal
```

Smoke the API:

```bash
task health             # /actuator/health
task version            # /api/v1/version
task openapi            # /openapi.json
```

## Repository layout

```
flycanon/
├── Dockerfile                # Multi-stage build, distroless-friendly runtime
├── Taskfile.yml              # Canonical dev-loop interface
├── docker-compose.yml        # api + worker + postgres + redis
├── docker-compose.test.yml   # Adds the mock LLM for integration tests
├── pyfly.yaml                # PyFly application configuration
├── alembic.ini               # Migration runner config
├── env_template              # Reference environment file (.env is gitignored)
├── migrations/               # Alembic versions
├── src/flycanon/
│   ├── app.py                # @pyfly_application + scan_packages
│   ├── main.py               # ASGI entry consumed by uvicorn
│   ├── cli.py                # ``flycanon {serve,worker,migrate}``
│   ├── config.py             # CanonSettings (FLYCANON_* env)
│   ├── core/                 # @configuration + services + mappers
│   ├── interfaces/           # Public DTOs + enums
│   ├── models/               # SQLAlchemy entities + repositories
│   ├── resources/prompts/    # YAML prompt templates
│   └── web/                  # @rest_controller + @controller_advice
├── sdks/
│   ├── python/               # Async-first Python SDK (Apache-2.0)
│   └── java/                 # Java SDK (Apache-2.0)
├── docs/                     # Architecture, payload reference, API reference
└── tests/
    ├── unit/
    └── integration/
```

## License

The service is proprietary -- see [`LICENSE`](LICENSE).

The SDKs under [`sdks/python`](sdks/python) and [`sdks/java`](sdks/java)
are released under the Apache License 2.0; each ships its own LICENSE
file.
