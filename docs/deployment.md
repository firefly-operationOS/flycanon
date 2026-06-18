<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Deployment**

</div>

---

flycanon ships as a **single Python service** plus its operational
dependencies. The reference deployment is one Docker image
(`ghcr.io/firefly-operationos/flycanon`) backed by a Postgres
instance with the `vector` extension and an embedding-provider key
of your choice. Everything else (Redis, Gotenberg, Docling OCR
sidecar, ...) is optional.

---

## Reference topologies

| Topology | Use case | Components |
|----------|----------|------------|
| **Single host (compose)** | Dev, demo, on-prem POC | `docker compose` with `flycanon` + `postgres(pgvector)` (+ optional `redis` for the EDA Redis adapter, `gotenberg` for Office conversion). |
| **Container platform (k8s, ECS, Cloud Run, ...)** | Production | One Deployment / Service / StatefulSet per role: API, worker, Postgres, optional Redis. |
| **Bare-metal** | Air-gapped operators | `uv run flycanon serve` + `uv run flycanon worker` + an external Postgres. |

The image, the env vars, and the migration step are identical across
all three. The differences are how you wire the dependencies.

---

## Pull the image

```bash
docker pull ghcr.io/firefly-operationos/flycanon:latest          # latest main
docker pull ghcr.io/firefly-operationos/flycanon:26.5.6          # specific release
docker pull ghcr.io/firefly-operationos/flycanon:26.5            # latest 26.5.x
```

Multi-arch manifest: pulling `:latest` on Apple Silicon /
AWS Graviton picks the `linux/arm64` layer; pulling on x86 picks
`linux/amd64`. Override explicitly with `--platform linux/amd64` if
needed.

---

## Roles

The same image runs three commands, selectable via the `CMD` /
`command:`:

| Command | What it does | Replica policy |
|---------|--------------|----------------|
| `serve` | FastAPI ASGI server on `:8500`. Stateless. | Horizontally scalable behind a load balancer. |
| `worker` | Subscribes to the EDA outbox / Redis / Kafka and processes async jobs (`ConsolidationRequested`, ...). | At least one. Many is fine -- the work-queue claim is idempotent. |
| `migrate` | Runs `alembic upgrade head` and exits. | Single shot, before `serve` / `worker` start on a new schema. |

The `docker-entrypoint.sh` shell switch maps these to the right
process call.

---

## docker-compose (reference)

The repo ships [`docker-compose.yml`](../docker-compose.yml) -- one
copy is enough for a single-host deployment. The composition is:

```
flycanon-api    -> 8500/tcp        (CMD ["serve"])
flycanon-worker                    (CMD ["worker"])
flycanon-pg     -> 5432/tcp        pgvector/pgvector:pg16
flycanon-redis  -> 6379/tcp        (optional; only with FLYCANON_EDA_ADAPTER=redis)
gotenberg       -> 3000/tcp        (optional; only with FLYCANON_OFFICE_CONVERTER=gotenberg)
```

```bash
docker compose --env-file .env up -d            # start everything
docker compose exec flycanon-api flycanon migrate   # apply migrations
curl -fsS http://localhost:8500/actuator/health | jq .
```

The compose file uses BuildKit named contexts so the build-from-source
path (`docker compose build`) works without copying the sibling
firefly framework repos into the flycanon tree -- see
[cicd.md § Build contexts](cicd.md#build-contexts).

---

## Environment

All keys are prefixed `FLYCANON_*`. The
[`env_template`](../env_template) file is the canonical reference;
copy it to `.env` (which is gitignored) and edit. The minimum
required for production:

| Key | What it is | Required? |
|-----|------------|-----------|
| `FLYCANON_DATABASE_URL` | Async-SQLAlchemy URL (`postgresql+asyncpg://user:pass@host:5432/db`) | **Yes** |
| `FLYCANON_EMBEDDING_MODEL` | `<provider>:<model>` (e.g. `openai:text-embedding-3-small`, `voyageai:voyage-large-2`, `ollama:nomic-embed-text`) | **Yes** |
| `FLYCANON_EMBEDDING_DIMENSIONS` | Dimensions of the chosen embedding model. **Must match `pgvector` index dimension** -- a mismatch produces a runtime error on first insert. | **Yes** |
| `FLYCANON_ANSWER_MODE` | Answer engine for `/api/v1/query`: `rlm` (default) or `rag` (deprecated, opt-in). See [Answer mode](#answer-mode-rlm-default--rag-deprecated). | Defaults to `rlm`. |
| `FLYCANON_ANSWER_MODEL` | `<provider>:<model>` for the **RAG** answer endpoint (default `anthropic:claude-sonnet-4-6`). Only used when `FLYCANON_ANSWER_MODE=rag`. | Needed only for `rag` mode. |
| `FLYCANON_ANSWER_FALLBACK_MODEL` | Used when the primary RAG model errors (e.g. provider 5xx, rate limit). | Recommended for `rag` mode. |
| `FLYCANON_STORE_ORIGINALS` | Persist original document bytes to the object store so RLM can reason over whole documents. **Required (`true`, the default) for RLM.** See [Answer mode](#answer-mode-rlm-default--rag-deprecated). | Defaults to `true`. |
| Provider API keys | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `VOYAGEAI_API_KEY`, `COHERE_API_KEY`, ... -- read by `fireflyframework-agentic` from env at boot. **`ANTHROPIC_API_KEY` is required at runtime in the default RLM mode** (the RLM engine calls the Anthropic Messages API directly). | As needed for your provider mix. |
| `FLYCANON_VECTOR_STORE` | Dense backend: `pgvector` (default), `qdrant` (`--extra qdrant`), or `chroma` (`--extra chroma`). | Defaults to `pgvector`. |
| `FLYCANON_EDA_ADAPTER` | `postgres` (default -- durable outbox + LISTEN/NOTIFY), `memory`, `redis`, `kafka`. | Defaults to `postgres`. |
| `FLYCANON_API_KEYS` | Comma-separated static API keys. When set, every `/api/v1/*` request requires `Authorization: Bearer <key>`. | Optional. |
| `FLYCANON_CORS_ORIGINS` | Comma-separated origins for `Access-Control-Allow-Origin`. | Optional. |

For a complete list with defaults and inline docs, run:

```bash
docker run --rm ghcr.io/firefly-operationos/flycanon:latest cat /app/env_template
```

---

## Answer mode (RLM default / RAG deprecated)

`FLYCANON_ANSWER_MODE` selects the engine for the non-streaming answer
path (`/api/v1/query`, `/api/v1/query:stream`, and the agent-tier
equivalents). `rlm` is the default; `rag` is opt-in and deprecated. Any
value other than `rag` is normalised to `rlm`.

| Key | What it is | Default |
|-----|------------|---------|
| `FLYCANON_ANSWER_MODE` | `rlm` (default) routes to the Recursive Language Model answerer; `rag` routes to the legacy hybrid-retrieval answerer. | `rlm` |
| `FLYCANON_RLM_ROOT_MODEL` | Orchestrator model that drives the CodeAct REPL loop. `<provider>:<model>`. | `anthropic:claude-sonnet-4-6` |
| `FLYCANON_RLM_SUB_MODEL` | Model for flat recursive sub-calls made from REPL code. | `anthropic:claude-sonnet-4-6` |
| `FLYCANON_RLM_ANSWER_MODEL` | Model for the final single-shot answer synthesis. | `anthropic:claude-sonnet-4-6` |
| `FLYCANON_RLM_MAX_ITERS` | Max orchestrator turns before the loop gives up and asks for a plain-text answer from the transcript. | `8` |
| `FLYCANON_RLM_SUB_BUDGET` | Total recursive sub-call budget across one root session. | `12` |
| `FLYCANON_RLM_MAX_DEPTH` | How deep `rlm(...)` may nest before degrading to a flat `llm`. | `1` |
| `FLYCANON_RLM_PROMPT_CACHE` | Mark the large static RLM system prompt with Anthropic `cache_control: ephemeral` so it is cached server-side and reused across the many Messages calls one CodeAct session makes (cuts input-token cost + per-call latency). `false` sends it as a plain string. | `true` |
| `FLYCANON_RLM_SANDBOX` | Where the model-written REPL code runs. `subprocess` (the secure default) execs it in a scrubbed-env, resource-limited child process; `inprocess` is the explicit opt-out that runs it in the engine's own restricted `exec` namespace (dev / trusted use only). Only the exact value `inprocess` opts out; anything else resolves to `subprocess`. See [RLM execution sandbox](#rlm-execution-sandbox-security). | `subprocess` |
| `FLYCANON_RLM_SANDBOX_TIMEOUT_S` | Per-turn wall-clock timeout (seconds) for the subprocess sandbox; the child is killed and the turn fails if exceeded. | `30` |

### What RLM is

The Recursive Language Model engine is a code-driven CodeAct REPL. A
root orchestrator model writes Python against the in-scope document
corpus (handed to it as a `docs` variable: `docs.keys()` lists the
documents, `docs[key]` returns the full text, `docs.pages(key)` returns
the page list for precise citation), makes recursive sub-calls on
slices of that corpus, and finishes by citing the filings / pages it
used. It reasons over **whole documents** rather than retrieving
chunks, which is why it depends on the object store below.

### RLM operational requirements

- **Originals persisted in the object store.** RLM reads the original
  document bytes, so intake must persist each original to the object
  store (`FLYCANON_STORE_ORIGINALS=true`, the default) and record its
  key on the source row. Sources without a stored original (no
  `object_store_key`) are silently skipped by the RLM corpus builder.
- **`ANTHROPIC_API_KEY` at runtime.** The RLM engine calls the
  Anthropic Messages API directly for all three RLM models; the
  `anthropic:` prefix is stripped before the id is sent.

### RAG deprecation

When `FLYCANON_ANSWER_MODE=rag`, the answer dispatcher emits a
server-side deprecation **warning log** on every RAG-mode answer:

```
FLYCANON_ANSWER_MODE=rag is deprecated and will be removed in a future release; RLM is the default
```

The legacy RAG answer path is slated for removal in a future release;
migrate to the default `rlm` mode. When `answer_mode=rag`, the answer
endpoints return an `X-Flycanon-Deprecation` response header to clients
-- the client-facing deprecation signal alongside the server log. The
`/api/v1/search` surface (raw hybrid
retrieval, no LLM) is unaffected and stays.

### RLM execution sandbox (security)

The RLM REPL **execs model-written Python** every turn, and the corpus is
**user-uploaded**, so a malicious document can prompt-inject the
orchestrator model into writing hostile code. To contain that, the exec
runs out of process by default (`FLYCANON_RLM_SANDBOX=subprocess`):

- **Scrubbed environment.** The child inherits only a minimal whitelist
  (`PATH`, `LANG`, `LC_ALL`, `PYTHONPATH`, `PYTHONHASHSEED`) -- no
  `ANTHROPIC_API_KEY`, no `AWS_*` / `AZURE_*` cloud creds, no
  `DATABASE_URL` / `REDIS_*`, and no `FLYCANON_*` secrets. Prompt-injected
  code cannot read a credential because none are present.
- **Resource limits.** At startup the child clamps itself with
  `setrlimit`: a CPU-time cap (`RLIMIT_CPU`), a 1 GiB address-space cap
  (`RLIMIT_AS`), and `RLIMIT_FSIZE = 0` so it cannot create or grow any
  file. The parent additionally enforces a wall-clock timeout
  (`FLYCANON_RLM_SANDBOX_TIMEOUT_S`, default 30 s) and SIGKILLs a child
  that overruns.
- **Capability RPC, no infrastructure in the child.** The child holds no
  document store or model client. Its `docs` / `llm` / `rlm` / `final`
  stubs marshal each call to the parent as a length-prefixed **JSON-only**
  frame (never `pickle` / `eval`), validated against fixed allowlists; the
  parent services it against the real infra and replies. The secrets,
  network, and infrastructure objects live only in the parent.
- **Blast radius.** An escape is limited to the in-scope corpus the parent
  already exposes through those capability RPCs -- the same documents the
  query was authorised to read.

Setting `FLYCANON_RLM_SANDBOX=inprocess` disables this isolation and runs
the model code in the engine's own process (restricted builtins only). It
is intended for dev / trusted environments and is **not recommended in
production**.

**Residual risk / follow-up.** The child is still a normal process on the
host network: a sandbox escape (or a bug in the restricted builtins) could
attempt **network egress to exfiltrate the in-scope corpus**. Hard
network/syscall blocking -- running the child under `nsjail` / `seccomp`
inside a network namespace with no egress -- is a recommended
defence-in-depth follow-up. It needs a `Dockerfile` change (the sandbox
tooling and a dropped-network profile) and is not yet in place.

---

## Object store (RLM document originals)

The object store holds the original document bytes the intake persisted
so RLM can replay them. `localfs` (default) writes files under a root
directory for dev / test; `s3` (requires `uv sync --extra s3`) writes to
a bucket. AWS credentials for the `s3` backend are read from the
standard environment (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
profiles / instance roles), not from these settings.

| Key | What it is | Default |
|-----|------------|---------|
| `FLYCANON_OBJECT_STORE_BACKEND` | `localfs` (default) or `s3`. | `localfs` |
| `FLYCANON_OBJECT_STORE_LOCALFS_ROOT` | Root directory for the `localfs` backend. | `./var/objects` |
| `FLYCANON_OBJECT_STORE_S3_BUCKET` | Bucket name for the `s3` backend. | _(empty)_ |
| `FLYCANON_OBJECT_STORE_S3_PREFIX` | Key prefix prepended to every object key within the bucket. | _(empty)_ |
| `FLYCANON_OBJECT_STORE_S3_ENDPOINT_URL` | Custom endpoint for MinIO / S3-compatible services; empty = default AWS. | _(empty)_ |
| `FLYCANON_OBJECT_STORE_S3_REGION` | Region; empty defers to boto3's standard region resolution. | _(empty)_ |
| `FLYCANON_STORE_ORIGINALS` | Whether intake persists original bytes and records the key on the source row. On by default so RLM has a whole-document corpus; a write failure is best-effort and never fails the ingest. | `true` |

---

## Corpus page cache (RLM)

The RLM corpus store fetches each in-scope filing's original from the
object store and PyMuPDF-extracts its page-structured text on first
access -- the expensive part of an RLM query. A shared, synchronous
page cache sits in front of that fetch so an in-scope filing is
fetched + extracted at most **once per process** (in-memory LRU) and,
with the Redis backend, **once per fleet**. Entries are keyed by the
source's `content_sha256`, so a re-ingested source (new bytes -> new
sha) misses the stale entry automatically -- there is no explicit
invalidation.

Backend selection mirrors the rate-limiter / idempotency stores:
`auto` (the default) uses Redis when `FLYCANON_REDIS_URL` is set,
in-memory otherwise; `redis` / `memory` force one or the other. The
Redis client is **synchronous** (the cache is read from the RLM
engine's worker thread). For multi-replica deployments set
`FLYCANON_REDIS_URL` so a single fetch on one replica warms the whole
fleet.

| Key | What it is | Default |
|-----|------------|---------|
| `FLYCANON_CORPUS_CACHE_BACKEND` | `auto` (Redis when `FLYCANON_REDIS_URL` set, else in-memory), `redis`, or `in_memory`. | `auto` |
| `FLYCANON_CORPUS_CACHE_TTL_S` | Per-entry TTL in seconds (both backends). | `3600` |
| `FLYCANON_CORPUS_CACHE_MAX_ENTRIES` | LRU cap for the in-memory backend (the Redis backend relies on native `EX` expiry). | `512` |

---

## Database

flycanon runs against **PostgreSQL with the `vector` extension**.
The reference docker-compose uses `pgvector/pgvector:pg16` so the
extension ships pre-installed.

```sql
CREATE DATABASE flycanon;
CREATE USER canon WITH PASSWORD 'canon';
GRANT ALL PRIVILEGES ON DATABASE flycanon TO canon;

\c flycanon
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO canon;
```

### Migrations

Alembic migrations live under [`migrations/`](../migrations). Run
once per deploy:

```bash
docker run --rm \
  --env FLYCANON_DATABASE_URL=postgresql+asyncpg://canon:canon@db.internal:5432/flycanon \
  ghcr.io/firefly-operationos/flycanon:latest migrate
```

`alembic upgrade head` is **also** run on container startup when
`RUN_MIGRATIONS=true` (the env_template default). Production deploys
typically prefer migrating as a separate step (`RUN_MIGRATIONS=false`
in the long-running containers, plus a one-shot `migrate` Job /
Container right before the API rollout).

### RLS roles

Migration `0013_rls_policies` emits `ALTER TABLE ... FORCE ROW LEVEL
SECURITY` on every `canon_*` table. The application connection must
**not** be a Postgres superuser (FORCE-RLS does not apply to
superusers), and the migration runner / background workers must
**bypass** the policy or they would see zero rows. Provision two
roles before pointing `FLYCANON_DATABASE_URL` at the cluster:

```sql
-- Admin role: runs migrations + cross-workspace workers
-- (consolidation re-embed sweep, retention reaper, EDA ingest worker).
CREATE ROLE flycanon_admin LOGIN PASSWORD 'change-me' BYPASSRLS;
GRANT ALL PRIVILEGES ON DATABASE flycanon TO flycanon_admin;
GRANT ALL ON SCHEMA public TO flycanon_admin;

-- App role: request-path engine. NO BYPASSRLS, NOT a superuser.
-- RLS policies filter by the per-session GUCs that
-- `install_tenant_guc_hook()` sets on each transaction.
CREATE ROLE flycanon_app LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE flycanon TO flycanon_app;
GRANT USAGE ON SCHEMA public TO flycanon_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public TO flycanon_app;
GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public TO flycanon_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO flycanon_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO flycanon_app;
```

Wire `FLYCANON_DATABASE_URL` at `flycanon_app` for the `serve` role
and at `flycanon_admin` for the `migrate` + `worker` roles. See
[architecture.md -> Row-level security](architecture.md#deployment-requirement)
for the rationale; the integration suite
(`tests/integration/test_rls_isolation.py`) exercises the
`BYPASSRLS` vs. `app_user` contract end-to-end.

### BM25 projection

flycanon is **Postgres-native** for both retrieval channels: BM25 rides
on a `tsvector` + GIN index on `canon_chunks.tsv` (generated column from
`canon_chunks.content`), and dense vectors live in `pgvector` in the same
Postgres. The deploy needs Postgres only.

---

## OCR engines

flycanon handles **both PDF kinds** (Full Digital Text PDFs and
PDF-Images) without caller flags -- see
[pipeline.md § PDF ingestion](pipeline.md#pdf-ingestion----two-kinds-one-pipeline)
for the routing detail.

| Engine | When | How to enable |
|--------|------|---------------|
| **Tesseract** (default) | Ships with the image. Apt packages: `tesseract-ocr-eng`, `-spa`, `-fra`, `-deu`, `-ita`, `-por`, `-cat`. | No-op -- it's the default. Languages default to `eng+spa`; override with `FLYCANON_OCR_LANG=eng+spa+fra`. |
| **Docling** | Layout-aware OCR with native multi-column / table handling. | Build a derived image: `RUN uv pip install --no-cache 'flycanon[docling]'`. Then run with `FLYCANON_PDF_OCR_ENGINE=docling`. Adds ~2.5 GB per architecture (PyTorch + HF wheels). |

The Docling extra is **not baked into the published image** for the
same reason it isn't on flydocs's: the size cost punishes the 95% of
deploys that don't need layout-aware OCR.

---

## Office conversion

Office formats (DOCX / XLSX / PPTX / ODT / ODS / ODP / RTF) are read
via native per-format loaders (python-docx, openpyxl, python-pptx,
odfpy, striprtf) by default. For high-fidelity extraction you can
render Office docs to PDF first:

| Converter | How | Trade-off |
|-----------|-----|-----------|
| `none` (default) | Native per-format loaders (python-docx / openpyxl / python-pptx / odfpy / striprtf). | Zero extra service. Text + structure fidelity for tables / images is best-effort. |
| `gotenberg` | HTTP sidecar (`gotenberg/gotenberg:8` -- see docker-compose). | High fidelity, distroless runtime stays clean. Adds one service. |
| `libreoffice` | In-container `soffice` subprocess. | High fidelity, no extra service. Bloats the runtime image (~1 GB). Build a derived image with `libreoffice-core`. |

Switch via `FLYCANON_OFFICE_CONVERTER=none|gotenberg|libreoffice`.

---

## Embedding providers

The embedding stack is **provider-agnostic** -- any
`fireflyframework-agentic` embedder identifier works. Set the model
id, the dimensions, and the provider's API key env var.

| Provider | Model id example | Dimensions | API key env |
|----------|------------------|-----------|-------------|
| OpenAI | `openai:text-embedding-3-small` | 1536 | `OPENAI_API_KEY` |
| OpenAI | `openai:text-embedding-3-large` | 3072 | `OPENAI_API_KEY` |
| VoyageAI | `voyageai:voyage-large-2` | 1536 | `VOYAGEAI_API_KEY` |
| Cohere | `cohere:embed-multilingual-v3.0` | 1024 | `COHERE_API_KEY` |
| Mistral | `mistral:mistral-embed` | 1024 | `MISTRAL_API_KEY` |
| Ollama (local) | `ollama:nomic-embed-text` | 768 | (none -- needs `OLLAMA_HOST`) |

**Dimensions must match the `pgvector` column.** flycanon's
migrations create `canon_chunk_vectors.embedding` as `vector(<dim>)`
at first boot using `FLYCANON_EMBEDDING_DIMENSIONS`. **Changing the
embedding model after data is loaded is a re-index** -- not a hot
swap.

---

## Authentication

flycanon ships two complementary auth modes; both come from pyfly:

| Mode | When | Config |
|------|------|--------|
| **Static API keys** | The simplest production gate. | `FLYCANON_API_KEYS=key1,key2,...` -- callers send `Authorization: Bearer <key>`. |
| **OAuth2 resource server** | Integrating with an existing IdP (Keycloak / Auth0 / Cognito / ...). | Set `pyfly.security.oauth2.resource-server.enabled=true` plus the provider's issuer URI in `pyfly.yaml`. |

Default deployment: both off (open). Production deployments **must**
enable at least one before exposing `/api/v1/*` to the network.

---

## Observability

* **Tracing.** W3C trace context (`traceparent`, `tracestate`) +
  correlation headers (`X-Correlation-Id`, `X-Request-Id`,
  `X-Tenant-Id`) are propagated automatically by pyfly's
  `CorrelationFilter` -- they ride into the audit log on every
  mutation.
* **Metrics.** Pyfly's actuator endpoints (`/actuator/metrics`,
  `/actuator/prometheus`) expose the standard JVM-style metrics
  (HTTP request totals + latency, async pool sizes, ...). Scrape
  them with Prometheus.
* **Health probes.** `/actuator/health/readiness` aggregates the
  database health indicator + the EDA event-publisher health
  indicator. `/actuator/health/liveness` is a process-up signal.
  Wire them as Kubernetes `readinessProbe` + `livenessProbe`.

```yaml
# Kubernetes example
readinessProbe:
  httpGet:
    path: /actuator/health/readiness
    port: 8500
  initialDelaySeconds: 5
  periodSeconds: 5
livenessProbe:
  httpGet:
    path: /actuator/health/liveness
    port: 8500
  periodSeconds: 10
```

---

## Storage sizing

flycanon does **not** store the canonical bytes -- only the
extracted text, chunks, embeddings, and metadata. Rough planning
numbers for the canonical store:

| Per source (Manifiesto.pdf-ish: 12 pages of digital text) | Bytes |
|-----------------------------------------------------------|-------|
| `canon_sources` row | ~2 KB |
| Extracted text (markdown) | ~25 KB |
| Chunks (avg 800 chars * 30 chunks) | ~24 KB |
| Embeddings (30 * 1536 dim * 4 bytes) | ~180 KB |
| **Total** | **~230 KB per source** |

A 10K-source corpus lands around **2.3 GB** before indexes. The
`pgvector` HNSW index doubles that. Plan accordingly.

---

## Backup / DR

The canonical state lives in **Postgres**. A pg_dump (or your cloud
provider's managed backup) captures everything flycanon needs to
resume:

* `canon_sources` (no bytes)
* `canon_chunks` (text + section path + page)
* `canon_chunk_vectors` (embeddings)
* `canon_knowledge_items` + `canon_knowledge_versions` + `canon_citations`
* `canon_candidates` + `canon_audit_events` + `canon_taxonomy_nodes`
* The `pyfly_eda_outbox` for in-flight EDA messages

The BM25 projection lives in Postgres (`canon_chunks.tsv` + GIN
index), so the pg_dump captures it too -- there is no separate
file-backed corpus to back up.

---

## Upgrades

Releases are CalVer (`YY.MM.PP`). The compatibility contract:

* **`YY.MM.x`** -- patch releases. Drop-in replacement; no schema
  change, no env-var change, no SDK breakage.
* **`YY.MM.0`** -- monthly release. May introduce new optional env
  vars or new endpoints, never breaks existing ones. Run `migrate`
  before the new image rolls out.
* **Major (year) bumps** -- explicitly documented breaking changes
  in the release notes.

The recommended upgrade dance:

```bash
# 1. Pull the new image
docker pull ghcr.io/firefly-operationos/flycanon:26.6.0

# 2. Run migrations against the live database
docker run --rm \
  --env-file .env \
  ghcr.io/firefly-operationos/flycanon:26.6.0 migrate

# 3. Roll the API + worker deployments
docker compose --env-file .env up -d
```

API responses are append-only (new fields land as additive); existing
SDK versions keep working against a newer service.

---

## Troubleshooting deploys

See [troubleshooting.md](troubleshooting.md) for a problem -> root
cause -> fix table covering the common production failure modes
(embeddings unavailable, pgvector dim mismatch, OCR missing, ...).
