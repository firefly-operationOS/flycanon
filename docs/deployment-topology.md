# Deployment Topology

> Master deployment guide for the **Firefly Intelligence System**
> (flycanon + flyradar). Audience: SRE / DevOps engineer setting the
> pair up from scratch in production. Mirrored byte-for-byte to both
> repos.

The platform narrative ("what these two services are, why they share
a vocabulary, how the unification lands") lives in
[firefly-intelligence-system.md](firefly-intelligence-system.md) --
this document does not re-state that material; it links to it.

Cross-references in this document:

- flycanon: [architecture](architecture.md) | [deployment](deployment.md) | [consumers](consumers.md) | [eda-events](eda-events.md)
- flyradar: [architecture](architecture.md) | [integration-with-flycanon](integration-with-flycanon.md)
- *(planned)* `operations-runbook.md` -- day-2 ops (token rotation, workspace lifecycle, backups, rolling restarts).
- *(planned)* `scale-and-performance.md` -- capacity planning and the
  partitioning thresholds referenced in section 9 below.

Throughout, image tags follow CalVer (`YY.MM.PP`). The current tags
at writing are `26.5.6` (flycanon) and `26.5.7` (flyradar); pin to
whatever lock-step pair your release process certifies (see each
repo's `CHANGELOG.md`).

---

## 1. System overview

The Firefly Intelligence System is **two stateful services + one
shared dependency (Postgres+pgvector)**. Optional components light up
based on adapter selection.

```
                                +-------------------------------------+
                                |        Operator / SDK / UI          |
                                |        (JWT -- user tier)           |
                                +--+----------------------------+-----+
                                   |                            |
                                   | HTTPS                      | HTTPS
                                   v                            v
+----------------------------------+-----+   +------------------+--------------------+
|              flycanon                   |   |              flyradar                |
|  +----------------+   +--------------+  |   |  +--------------+   +--------------+ |
|  |    API pod     |   |  Worker pod  |  |   |  |   API pod    |   |  Worker pod  | |
|  |  (CMD: serve)  |   | (CMD: worker)|  |   |  | (CMD: serve) |   | (CMD: worker)| |
|  | :8500          |   |              |  |   |  | :8500        |   |              | |
|  +-------+--------+   +------+-------+  |   |  +------+-------+   +------+-------+ |
|          |                   |          |   |         |                  |         |
|          | role: flycanon_app|          |   |         | role: flyradar_app         |
|          |   (RLS-bound)     | role:    |   |         |   (RLS-bound)    |         |
|          |                   | flycanon |   |         |                  | role:   |
|          |                   | _admin   |   |         |                  | flyradar|
|          |                   |(BYPASSRLS|   |         |                  | _admin  |
|          v                   v          |   |         v                  v         |
+----------+-------------------+----------+   +---------+------------------+---------+
                |                                       |
                |                                       |
                v                                       v
+--------------------------------+    +----------------------------------+
|  Postgres + pgvector (canon)   |    |  Postgres + pgvector (radar)     |
|  db: flycanon                  |    |  db: flyradar                    |
|  - canon_* tables (RLS)        |    |  - flyradar_* tables (RLS)       |
|  - canon_chunk_vectors (HNSW)  |    |  - vector tables                 |
|  - pyfly_eda_outbox            |    |  - pyfly_eda_outbox              |
+--------------------------------+    +----------------------------------+
                ^                                       ^
                |                                       |
                | LISTEN/NOTIFY                         | LISTEN/NOTIFY
                | (default EDA adapter)                 | (default EDA adapter)
                v                                       v
+------------------------------------------------------------------------+
|              Optional: Kafka or RabbitMQ (when EDA_ADAPTER=kafka)      |
|              - canon.workspaces.v1, flycanon.ingest, ...               |
+------------------------------------------------------------------------+

                            +------------------------------+
                            |  Optional: Redis             |
                            |  - planned: shared agent     |
                            |    token rate-limit counters |
                            |  - planned: shared idempotency|
                            |    store across replicas     |
                            +------------------------------+

Cross-service network flows
============================
                                          +--+
        POST /api/v1/agent/canon/handoff  |  |
flyradar ---------------------------------> flycanon       (token + Idempotency-Key)
        POST /api/v1/agent/sources         |  |
                                           +--+
        GET  /api/v1/workspaces/{id}      +--+
flyradar ---------------------------------> flycanon       (cache miss fallback)
                                           +--+
        canon.workspaces.v1 EDA topic     +--+
flyradar <--------------------------------- flycanon       (refresh cache)
                                           +--+
```

The full producer/consumer matrix and per-route detail is in
[firefly-intelligence-system.md § Cross-service flows](firefly-intelligence-system.md#cross-service-flows).

**Shared dependencies summary:**

| Dependency | flycanon | flyradar | Mandatory? |
|------------|---------|---------|-----------|
| Postgres + `vector` extension | Yes (canon DB) | Yes (radar DB) | Yes |
| EDA broker (Postgres outbox / Kafka / RabbitMQ / Redis Streams) | Yes | Yes | Yes -- the in-process outbox over the service's own Postgres is the default and ships out of the box |
| Redis | No (optional EDA adapter, optional Ollama sidecar key store) | No (optional EDA adapter) | No today. Planned for shared rate-limit + idempotency in a future release |
| Gotenberg (Office -> PDF) | Optional (`FLYCANON_OFFICE_CONVERTER=gotenberg`) | No | No |
| Ollama (local embeddings) | Optional profile | No | No |

---

## 2. Deployment topologies

Three reference layouts. Pick the one that matches your org's
operational posture.

### Topology A: Single-host development / demo

One docker-compose stack on one box. Targets dev, demo, on-prem POC.

References:

- flycanon: [`flycanon/docker-compose.yml`](../docker-compose.yml) (when reading this doc inside the flycanon repo) -- api + worker + Postgres + Redis (and optional Ollama / Gotenberg profiles).
- flyradar: [`flyradar/docker-compose.yml`](../docker-compose.yml) (when inside the flyradar repo) -- api + worker + Postgres.
- Cross-service e2e: [`flyradar/docker-compose.e2e.yml`](../docker-compose.e2e.yml) -- single Postgres+pgvector container hosting both `flycanon` and `flyradar` databases with split admin/app roles. This is the canonical reference for "both services against one Postgres".

| Component | RAM | CPU | Disk |
|-----------|----|----|-----|
| flycanon api + worker | 2 GB total | 1 vCPU | -- |
| flyradar api + worker | 2 GB total | 1 vCPU | -- |
| Postgres (pgvector/pgvector:pg16) | 2 GB | 1 vCPU | 20 GB to start; grows linearly per ingested source (~230 KB per source -- see flycanon [deployment.md § Storage sizing](deployment.md#storage-sizing)). |

Boot order is enforced by `depends_on: service_healthy` in the
compose files; nothing further to wire by hand.

### Topology B: Kubernetes basics

One Deployment + Service per role, one pod per Deployment. Postgres
is managed (RDS / Cloud SQL / Aiven). EDA stays on the Postgres
outbox per service (no separate broker).

```
namespace: firefly-intel
  Deployment/flycanon-api      replicas=1   command: ["serve"]
  Deployment/flycanon-worker   replicas=1   command: ["worker"]
  Deployment/flyradar-api      replicas=1   command: ["serve"]
  Deployment/flyradar-worker   replicas=1   command: ["worker"]
  Job/flycanon-migrate         (one-shot per release, command: ["migrate"])
  Job/flyradar-migrate         (one-shot per release, command: ["migrate"])
  Service/flycanon  ClusterIP  :8500
  Service/flyradar  ClusterIP  :8500
  Secret/flycanon-env          provider keys + agent token
  Secret/flyradar-env          provider keys + FLYRADAR_FLYCANON_AGENT_TOKEN
  ConfigMap/flycanon-app       FLYCANON_* tunables
  ConfigMap/flyradar-app       FLYRADAR_* tunables
```

Pod resource hints (1 replica each):

| Pod | Request | Limit | Notes |
|-----|---------|-------|-------|
| flycanon-api | 500m CPU / 1 Gi RAM | 1 CPU / 2 Gi RAM | Stateless. |
| flycanon-worker | 500m CPU / 1 Gi RAM | 1 CPU / 2 Gi RAM | Idempotent claim; horizontally scalable. |
| flyradar-api | 500m CPU / 1 Gi RAM | 1 CPU / 2 Gi RAM | Stateless. |
| flyradar-worker | 500m CPU / 1 Gi RAM | 1 CPU / 2 Gi RAM | Heartbeat file healthcheck (see worker env vars in section 5). |
| Postgres | (managed) | (managed) | `db.t3.medium` / `db-custom-2-7680` is enough for tens-of-thousands of sources. Scale up by reads-per-second, not by data volume. |

NetworkPolicy:

- `flycanon-api`, `flycanon-worker` -- egress to Postgres only (plus LLM provider FQDNs).
- `flyradar-api`, `flyradar-worker` -- egress to Postgres + flycanon (port 8500) only (plus LLM provider FQDNs).
- Ingress on each Service from cluster ingress controller + sibling service.

Readiness / liveness:

```yaml
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

### Topology C: Production HA

For multi-tenant production: more than one replica per role, Postgres
with read replicas, Kafka as the EDA broker (so workspace events fan
out to consumers other than flyradar without coupling them to
flycanon's database).

```
flycanon-api      replicas>=3     (rolling, horizontal autoscale on req/s)
flycanon-worker   replicas>=2     (work-queue claim is idempotent)
flyradar-api      replicas>=3
flyradar-worker   replicas>=2     (or a dedicated worker StatefulSet
                                   if you need pinned identity for
                                   long-running discovery jobs)
flycanon-migrate  one-shot Job per release
flyradar-migrate  one-shot Job per release

Postgres
  primary  (writes + request path)
  read-replica x2  (reporting / billing / audit-tail reads only;
                    read-replica routing for the request path is on
                    the platform roadmap, not plumbed in code today)

Kafka  topics: canon.workspaces.v1, flycanon.ingest,
               flycanon.knowledge, flycanon.audit,
               flyradar.jobs.v1
```

Pod resource hints (per replica, HA):

| Pod | Request | Limit | Notes |
|-----|---------|-------|-------|
| flycanon-api | 1 CPU / 2 Gi RAM | 2 CPU / 4 Gi RAM | Bump for high-QPS RAG (`/api/v1/query` is the heaviest). |
| flycanon-worker | 1 CPU / 2 Gi RAM | 2 CPU / 4 Gi RAM | Embedding fan-out + consolidation. |
| flyradar-api | 1 CPU / 2 Gi RAM | 2 CPU / 4 Gi RAM | |
| flyradar-worker | 2 CPU / 4 Gi RAM | 4 CPU / 8 Gi RAM | Discovery pipeline is CPU-heavy (LLM concurrency caps it). |

Stop-grace must be at least `FLYRADAR_WORKER_SHUTDOWN_TIMEOUT_S`
(default 30) for radar workers and `FLYCANON_WORKER_SHUTDOWN_GRACE_S`
(default 30) for canon workers, so SIGTERM lets in-flight work drain
cleanly. (The Dockerfiles already do the right thing -- Kubernetes
just needs `terminationGracePeriodSeconds: 45` on each worker
Deployment.)

NetworkPolicy:

- `flycanon-*` -- egress to Postgres, Kafka brokers, LLM provider FQDNs.
- `flyradar-*` -- egress to Postgres, Kafka brokers, flycanon Service, LLM provider FQDNs.

---

## 3. Postgres role provisioning (canonical SQL)

Both services share the same role model: a `_admin` role with
`BYPASSRLS` for migrations + cross-workspace workers, and a `_app`
role without `BYPASSRLS` for the request path. The split is
**mandatory** in production: `FORCE ROW LEVEL SECURITY` applies to
the table owner but not to superusers, so an app role that is a
superuser silently bypasses every policy.

The block below is the canonical setup; it matches
[`flyradar/tests/e2e/init-databases.sql`](../tests/e2e/init-databases.sql)
(when reading inside flyradar) one-to-one. Run it against the
Postgres admin URL (`postgres` database) once per cluster.

```sql
-- ============================================================
-- Firefly Intelligence System: Postgres provisioning
-- One Postgres cluster, two logical databases, two roles each.
-- ============================================================

CREATE DATABASE flycanon;
CREATE DATABASE flyradar;

-- ------------------------------------------------------------
-- flycanon roles
-- ------------------------------------------------------------
CREATE ROLE flycanon_admin LOGIN PASSWORD 'change-me-admin' BYPASSRLS;
CREATE ROLE flycanon_app   LOGIN PASSWORD 'change-me-app';

GRANT ALL PRIVILEGES ON DATABASE flycanon TO flycanon_admin;
GRANT CONNECT          ON DATABASE flycanon TO flycanon_app;

\connect flycanon;

CREATE EXTENSION IF NOT EXISTS vector;

GRANT USAGE ON SCHEMA public TO flycanon_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public TO flycanon_app;
GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public TO flycanon_app;

-- Tables / sequences created LATER by alembic (running as the admin
-- role) need the same grants automatically.
ALTER DEFAULT PRIVILEGES FOR ROLE flycanon_admin
  IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLES TO flycanon_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flycanon_admin
  IN SCHEMA public GRANT USAGE, SELECT
  ON SEQUENCES TO flycanon_app;

-- ------------------------------------------------------------
-- flyradar roles
-- ------------------------------------------------------------
\connect postgres;

CREATE ROLE flyradar_admin LOGIN PASSWORD 'change-me-admin' BYPASSRLS;
CREATE ROLE flyradar_app   LOGIN PASSWORD 'change-me-app';

GRANT ALL PRIVILEGES ON DATABASE flyradar TO flyradar_admin;
GRANT CONNECT          ON DATABASE flyradar TO flyradar_app;

\connect flyradar;

CREATE EXTENSION IF NOT EXISTS vector;

GRANT USAGE ON SCHEMA public TO flyradar_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public TO flyradar_app;
GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public TO flyradar_app;

ALTER DEFAULT PRIVILEGES FOR ROLE flyradar_admin
  IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLES TO flyradar_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyradar_admin
  IN SCHEMA public GRANT USAGE, SELECT
  ON SEQUENCES TO flyradar_app;
```

> **Note on the e2e harness:** the version in
> `flyradar/tests/e2e/init-databases.sql` uses `SUPERUSER` on the
> admin roles instead of `BYPASSRLS` because the test container also
> needs to `CREATE EXTENSION` and run other DDL the policy-bound role
> could not. In production, prefer `BYPASSRLS` over `SUPERUSER` --
> the extension only needs to be created once during bootstrap.

`FLYCANON_DATABASE_URL` / `FLYRADAR_DATABASE_URL` should point at the
`_admin` role today (the request-path engine reads tenant GUCs via
the `after_begin` SQLAlchemy listener so it relies on `_admin` not
having the `BYPASSRLS` for tenant filtering only when the admin role
is split off via the planned `_APP_DATABASE_URL` env var). The
forward-compatible posture documented in
[`flyradar/env_template`](../env_template) is to ship the
`FLYRADAR_APP_DATABASE_URL` second URL once that split lands in
`CanonSettings` / `FlyradarSettings`. See the
"BYPASSRLS split" comment block in the flyradar env template for the
contract; flycanon mirrors the same pattern.

---

## 4. Migration ordering

Both services use Alembic; migrations live under `migrations/` in each
repo.

### On every deploy

1. **Run migrations as the admin role** (`BYPASSRLS`) for each
   service, **before** rolling traffic to the new image:

   ```bash
   docker run --rm \
     -e FLYCANON_DATABASE_URL=postgresql+asyncpg://flycanon_admin:...@db/flycanon \
     ghcr.io/firefly-operationos/flycanon:26.5.6 migrate

   docker run --rm \
     -e FLYRADAR_DATABASE_URL=postgresql+asyncpg://flyradar_admin:...@db/flyradar \
     ghcr.io/firefly-operationos/flyradar:26.5.7 migrate
   ```

   The Dockerfile entrypoint of both services accepts `migrate` as a
   subcommand (`./docker-entrypoint.sh migrate` -> `alembic upgrade head`
   and exit). See
   [`flycanon/docker-entrypoint.sh`](../docker-entrypoint.sh) and
   [`flyradar/docker-entrypoint.sh`](../docker-entrypoint.sh).

2. **Wait for completion.** Both `migrate` containers exit with code 0
   on success and non-zero on failure -- treat them as a release gate.

3. **Roll traffic** to the new `serve` and `worker` replicas.

`RUN_MIGRATIONS=true` (the env_template default) also runs `alembic
upgrade head` on container startup, but production deploys
typically prefer migrating as a separate step (set
`RUN_MIGRATIONS=false` in the long-running containers and use the
one-shot Job / Container above).

### Order between services

When upgrading both services at once: **migrate flycanon first**,
roll flycanon API + worker, then migrate flyradar and roll
flyradar. The reason is one-way: flyradar's handoff calls
`POST /api/v1/agent/sources` on flycanon, so flycanon must speak
the newest wire shape before flyradar starts emitting it. The same
applies to the workspace-cache fallback (`GET /api/v1/workspaces/{id}`).
Going the other way (radar new, canon old) can produce 400s during
the rollout window.

---

## 5. Required env vars (per service)

The canonical reference is each repo's `env_template`. The table
below records the vars an SRE needs to set explicitly; everything
else has a working default. Every row was verified against
`flycanon/env_template` and `flyradar/env_template` at writing.

### flycanon

| Var | Required? | Default | Notes |
|-----|-----------|---------|-------|
| `FLYCANON_DATABASE_URL` | yes | `postgresql+asyncpg://canon:canon@localhost:5432/flycanon` (dev) | Production: point at `flycanon_admin` (the `after_begin` listener applies tenant GUCs per transaction). The planned `_APP_DATABASE_URL` split documented in flyradar's env template mirrors here. |
| `RUN_MIGRATIONS` | no | `true` | Set `false` in long-running containers when you run a separate `migrate` Job (recommended in production). |
| `FLYCANON_PORT` | no | `8500` | API listen port. |
| `FLYCANON_LOG_LEVEL` | no | `INFO` | Standard Python logging level. |
| `FLYCANON_EDA_ADAPTER` | no | `postgres` | `memory` (single-process dev), `postgres` (durable outbox + LISTEN/NOTIFY -- default), `redis` (Redis Streams), `kafka` (production fan-out). |
| `FLYCANON_REDIS_URL` | only if `EDA_ADAPTER=redis` | `redis://localhost:6379/0` | Single connection URL. |
| `FLYCANON_INGEST_TOPIC` | no | `flycanon.ingest` | EDA topic name. |
| `FLYCANON_KNOWLEDGE_TOPIC` | no | `flycanon.knowledge` | EDA topic name. |
| `FLYCANON_AUDIT_TOPIC` | no | `flycanon.audit` | EDA topic name. |
| `FLYCANON_WORKER_MAX_CONCURRENCY` | no | `8` | Inflight handler tasks cap on the EDA worker. |
| `FLYCANON_WORKER_HANDLER_TIMEOUT_S` | no | `120` | Per-handler wall clock cap. |
| `FLYCANON_WORKER_SHUTDOWN_GRACE_S` | no | `30` | SIGTERM drain window. Kubernetes `terminationGracePeriodSeconds` must be >= this. |
| `FLYCANON_EMBEDDING_MODEL` | yes | `openai:text-embedding-3-small` | Any pydantic-ai / fireflyframework-agentic embedder identifier. |
| `FLYCANON_EMBEDDING_DIMENSIONS` | yes | `1536` | Must match the chosen model; pgvector column shape is sealed on first insert. |
| `FLYCANON_ANSWER_MODEL` | yes for `/api/v1/query` | `anthropic:claude-sonnet-4-6` | RAG answer model. |
| `FLYCANON_ANSWER_FALLBACK_MODEL` | recommended | `openai:gpt-4o` | Used when primary errors (5xx / rate limit). |
| `FLYCANON_AGENT_MAX_OUTPUT_TOKENS` | no | `8192` | Per-agent output cap. Anthropic / OpenAI default to 4096 which silently truncates the consolidator's structured candidate arrays. |
| `FLYCANON_RETRIEVAL_TOP_K` | no | `10` | Retrieved chunks returned to caller. |
| `FLYCANON_RETRIEVAL_PER_QUERY_K` | no | `30` | Per-channel (BM25, dense) candidates before fusion. |
| `FLYCANON_RETRIEVAL_RRF_K` | no | `60` | Reciprocal Rank Fusion constant. |
| `FLYCANON_RERANKER_MODEL` | no | (empty -- disabled) | Optional cross-encoder reranker (`cohere:rerank-multilingual-v3.0`, `voyageai:rerank-2`). |
| `FLYCANON_QUERY_EXPANSION_ENABLED` | no | `false` | Multi-query expansion (+1 LLM call per `/search` and `/query`). |
| `FLYCANON_PII_SCANNER` | no | `regex` | `regex`, `presidio`, or `disabled`. |
| `FLYCANON_PII_POLICY` | no | `warn` | `warn`, `redact`, `reject`. |
| `FLYCANON_VECTOR_STORE` | no | `pgvector` | `pgvector` (default; HNSW in same Postgres), `sqlite-vec`, `chroma`, `qdrant`, `pinecone`, `memory`. |
| `FLYCANON_CORPUS_PATH` | no | `./local_data/corpus.db` | Only material for `sqlite-vec`; for `pgvector` the BM25 projection rides on `tsvector` in Postgres. |
| `FLYCANON_PGVECTOR_TABLE` | no | `canon_chunk_vectors` | |
| `FLYCANON_PGVECTOR_HNSW_M` | no | `16` | HNSW degree. |
| `FLYCANON_PGVECTOR_HNSW_EF_CONSTRUCTION` | no | `64` | HNSW build-time candidate list. |
| `FLYCANON_CHUNK_SIZE_TOKENS` | no | `1200` | |
| `FLYCANON_CHUNK_OVERLAP_TOKENS` | no | `150` | |
| `FLYCANON_CHUNK_STRATEGY` | no | `paragraph` | |
| `FLYCANON_MAX_BYTES` | no | `33554432` (32 MiB) | Per-source size cap after upload. |
| `FLYCANON_BINARY_NORMALIZE_ENABLED` | no | `true` | Master kill-switch for the binary normaliser. |
| `FLYCANON_BINARY_MAX_RECURSION_DEPTH` | no | `4` | Archive / email expansion. |
| `FLYCANON_BINARY_MAX_EXPANDED_FILES` | no | `50` | Defends against zip-bomb fan-out. |
| `FLYCANON_OFFICE_CONVERTER` | no | `none` | `none` (MarkItDown), `gotenberg`, `libreoffice`. |
| `FLYCANON_GOTENBERG_URL` | only if `OFFICE_CONVERTER=gotenberg` | `http://gotenberg:3000` | Sidecar URL. |
| `FLYCANON_OCR_LANG` | no | `eng+spa` | `+`-joined ISO 639-2/B codes. |
| `FLYCANON_API_KEYS` | no | (empty) | Comma-separated static API keys (open if empty -- guard with the IdP integration in `pyfly.yaml` instead). |
| `OPENAI_API_KEY` | conditional | (none) | Required if any embedding / answer / reranker model is OpenAI. |
| `ANTHROPIC_API_KEY` | conditional | (none) | Required if any answer model is Anthropic. |

### flyradar

| Var | Required? | Default | Notes |
|-----|-----------|---------|-------|
| `FLYRADAR_DATABASE_URL` | yes | `postgresql+asyncpg://radar:radar@localhost:5432/flyradar` (dev) | Admin role URL. The migration runner + cross-workspace workers (retention sweep, discovery worker) use this connection. |
| `FLYRADAR_APP_DATABASE_URL` | recommended in production | (unset -- shares the admin URL) | App role URL (`flyradar_app`, no BYPASSRLS). When set, request-path repositories use this connection so RLS policies fire. Leave unset for dev / single-process runs. |
| `RUN_MIGRATIONS` | no | `true` | Set `false` in long-running containers when running a separate `migrate` Job. |
| `FLYRADAR_PORT` | no | `8500` | API listen port. |
| `FLYRADAR_LOG_LEVEL` | no | `INFO` | |
| `FLYRADAR_EDA_ADAPTER` | no | `postgres` | `memory`, `postgres` (default), `redis`, `kafka`. Mirror the flycanon adapter unless you have a reason not to. |
| `FLYRADAR_REDIS_URL` | only if `EDA_ADAPTER=redis` | `redis://localhost:6379/0` | |
| `FLYRADAR_JOBS_TOPIC` | no | `flyradar.jobs` | EDA topic for async discovery jobs. |
| `FLYRADAR_JOBS_EVENT_TYPE` | no | `DiscoveryJobSubmitted` | |
| `FLYRADAR_JOBS_COMPLETED_EVENT_TYPE` | no | `DiscoveryJobCompleted` | |
| `FLYRADAR_MODEL` | yes | `anthropic:claude-sonnet-4-6` | Primary LLM for the discovery pipeline. |
| `FLYRADAR_FALLBACK_MODEL` | recommended | `openai:gpt-4o` | Fallback when primary errors. |
| `FLYRADAR_SYNC_TIMEOUT_S` | no | `120` | Per-call sync timeout. |
| `FLYRADAR_ASYNC_TIMEOUT_S` | no | `1800` | Async job total wall clock. |
| `FLYRADAR_MAX_ARTIFACT_BYTES` | no | `33554432` | 32 MiB per artifact. |
| `FLYRADAR_MAX_ARTIFACTS_SYNC` | no | `50` | |
| `FLYRADAR_MAX_EVENTS_SYNC` | no | `200000` | |
| `FLYRADAR_EXTRACTION_TIMEOUT_S` | no | `600` | Per-stage timeout (one of several). |
| `FLYRADAR_MINING_TIMEOUT_S` | no | `180` | |
| `FLYRADAR_DUPLICITY_TIMEOUT_S` | no | `180` | |
| `FLYRADAR_CONTRADICTION_TIMEOUT_S` | no | `180` | |
| `FLYRADAR_DEPENDENCY_TIMEOUT_S` | no | `120` | |
| `FLYRADAR_ROOTCAUSE_TIMEOUT_S` | no | `300` | |
| `FLYRADAR_GAP_TIMEOUT_S` | no | `180` | |
| `FLYRADAR_PERSONA_TIMEOUT_S` | no | `120` | |
| `FLYRADAR_REPORTS_TIMEOUT_S` | no | `300` | |
| `FLYRADAR_JOB_MAX_ATTEMPTS` | no | `3` | Includes the initial attempt. |
| `FLYRADAR_RETRY_BASE_DELAY_S` | no | `5.0` | Capped-exponential backoff floor. |
| `FLYRADAR_RETRY_MAX_DELAY_S` | no | `300.0` | Capped-exponential backoff ceiling. |
| `FLYRADAR_WORKER_SHUTDOWN_TIMEOUT_S` | no | `30` | SIGTERM drain window. `stop_grace_period` (Docker) / `terminationGracePeriodSeconds` (k8s) must be >= this. |
| `FLYRADAR_WORKER_HEARTBEAT_PATH` | no | `/tmp/flyradar_worker_healthy` | Worker touches this file on every heartbeat tick; docker / k8s healthcheck reads its mtime. |
| `FLYRADAR_WORKER_HEARTBEAT_INTERVAL_S` | no | `5` | |
| `FLYRADAR_WEBHOOK_TIMEOUT_S` | no | `15` | Job-completion callback timeout. |
| `FLYRADAR_WEBHOOK_MAX_ATTEMPTS` | no | `5` | |
| `FLYRADAR_WEBHOOK_RETRY_BASE_DELAY_S` | no | `1.0` | |
| `FLYRADAR_WEBHOOK_RETRY_MAX_DELAY_S` | no | `60.0` | |
| `FLYRADAR_WEBHOOK_HMAC_SECRET` | no | (empty) | When set, callback body is HMAC-signed. |
| `FLYRADAR_DUPLICITY_SIMILARITY_THRESHOLD` | no | `0.82` | |
| `FLYRADAR_ROOTCAUSE_COST_WEIGHT` | no | `0.4` | |
| `FLYRADAR_ROOTCAUSE_FREQUENCY_WEIGHT` | no | `0.4` | |
| `FLYRADAR_ROOTCAUSE_ACTIONABILITY_WEIGHT` | no | `0.2` | |
| `FLYRADAR_FLYCANON_BASE_URL` | yes for handoff + workspace cache | `http://localhost:8500` (dev only) | flycanon's externally-reachable origin (e.g. `http://flycanon:8500` in compose, `https://canon.example.com` in production). |
| `FLYRADAR_FLYCANON_AGENT_TOKEN` | yes for handoff | (unset) | Long-lived secret minted via flycanon's `POST /api/v1/agent-tokens`. Set as a Kubernetes Secret / Parameter Store entry. Never echoed back -- treat as production-secret material. |
| `FLYRADAR_FLYCANON_TIMEOUT_S` | no | `30.0` | HTTP timeout for canon calls. |
| `FLYRADAR_WORKSPACE_CACHE_TTL_SECONDS` | no | `300` | 5-minute TTL on the workspace metadata cache. `0` disables caching. |
| `FLYRADAR_WORKSPACE_CACHE_MAX_ENTRIES` | no | `1000` | LRU cap; oldest entry evicted on overflow. |
| `FLYRADAR_WORKSPACE_TOPIC` | no | `canon.workspaces.v1` | EDA topic to subscribe to. Mirrors flycanon's emit topic. |
| `FLYRADAR_WORKSPACE_CREATED_EVENT_TYPE` | no | `WorkspaceCreated` | Override only if flycanon renames the wire type. |
| `FLYRADAR_WORKSPACE_UPDATED_EVENT_TYPE` | no | `WorkspaceUpdated` | |
| `FLYRADAR_WORKSPACE_DELETED_EVENT_TYPE` | no | `WorkspaceDeleted` | |
| `ANTHROPIC_API_KEY` | conditional | (unset) | Required if `FLYRADAR_MODEL` or `FLYRADAR_FALLBACK_MODEL` is Anthropic. |
| `OPENAI_API_KEY` | conditional | (unset) | Required if `FLYRADAR_MODEL` or `FLYRADAR_FALLBACK_MODEL` is OpenAI. |
| `GOOGLE_API_KEY` | conditional | (unset) | Required if either model is Google. |
| `MISTRAL_API_KEY` | conditional | (unset) | Required if either model is Mistral. |

---

## 6. Day-1 setup walkthrough

Step-by-step. Replace placeholders with real hostnames / passwords /
tags before running.

```bash
# ============================================================
# Variables (set these once)
# ============================================================
export PGHOST=db.example.com
export PGPORT=5432
export PG_ADMIN_USER=admin
export PG_ADMIN_PASS=admin
export CANON_TAG=26.5.6
export RADAR_TAG=26.5.7
export TENANT_ID=acme
export WORKSPACE_ID=ws-prod


# ============================================================
# 1. Provision Postgres + roles (one-off)
# ============================================================
# Save the SQL from section 3 of this doc as init-databases.sql.
psql "postgresql://${PG_ADMIN_USER}:${PG_ADMIN_PASS}@${PGHOST}:${PGPORT}/postgres" \
  -f init-databases.sql


# ============================================================
# 2. Apply migrations (as the admin / BYPASSRLS role)
# ============================================================
docker run --rm \
  -e FLYCANON_DATABASE_URL="postgresql+asyncpg://flycanon_admin:change-me-admin@${PGHOST}:${PGPORT}/flycanon" \
  -e RUN_MIGRATIONS=false \
  ghcr.io/firefly-operationos/flycanon:${CANON_TAG} migrate

docker run --rm \
  -e FLYRADAR_DATABASE_URL="postgresql+asyncpg://flyradar_admin:change-me-admin@${PGHOST}:${PGPORT}/flyradar" \
  -e RUN_MIGRATIONS=false \
  ghcr.io/firefly-operationos/flyradar:${RADAR_TAG} migrate


# ============================================================
# 3. Boot flycanon (must come up before flyradar -- flyradar's
#    cache + handoff depend on it)
# ============================================================
docker run -d \
  --name flycanon-api \
  -e FLYCANON_DATABASE_URL="postgresql+asyncpg://flycanon_admin:change-me-admin@${PGHOST}:${PGPORT}/flycanon" \
  -e RUN_MIGRATIONS=false \
  -e FLYCANON_EMBEDDING_MODEL="openai:text-embedding-3-small" \
  -e FLYCANON_EMBEDDING_DIMENSIONS=1536 \
  -e FLYCANON_ANSWER_MODEL="anthropic:claude-sonnet-4-6" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -p 8500:8500 \
  ghcr.io/firefly-operationos/flycanon:${CANON_TAG}

# Wait for readiness.
until curl -fsS http://localhost:8500/actuator/health/readiness >/dev/null; do
  sleep 2
done


# ============================================================
# 4. Mint flyradar's flycanon agent token (manual one-time)
#
# Replace ${OPERATOR_JWT} with a JWT that the canon authorizer
# accepts for the (tenant, workspace) pair. In dev with auth off
# the Authorization header can be omitted.
# ============================================================
RESPONSE=$(curl -fsS -X POST http://localhost:8500/api/v1/agent-tokens \
  -H "Authorization: Bearer ${OPERATOR_JWT}" \
  -H "X-Tenant-Id: ${TENANT_ID}" \
  -H "X-Workspace-Id: ${WORKSPACE_ID}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "flyradar-prod",
    "scopes": [
      "agent.sources:ingest",
      "agent.sources:read",
      "agent.knowledge:read",
      "agent.candidates:propose"
    ],
    "workspace_allowlist": ["'"${WORKSPACE_ID}"'"],
    "expires_at": "2027-05-22T00:00:00Z"
  }')

# The "token" field is returned exactly ONCE. Capture it and store
# it in your secret manager (Vault / sealed secret / Parameter Store).
echo "${RESPONSE}" | jq -r '.token' > flyradar-canon-agent-token.secret


# ============================================================
# 5. Boot flyradar with the minted token
# ============================================================
docker run -d \
  --name flyradar-api \
  -e FLYRADAR_DATABASE_URL="postgresql+asyncpg://flyradar_admin:change-me-admin@${PGHOST}:${PGPORT}/flyradar" \
  -e RUN_MIGRATIONS=false \
  -e FLYRADAR_FLYCANON_BASE_URL="http://flycanon-api:8500" \
  -e FLYRADAR_FLYCANON_AGENT_TOKEN="$(cat flyradar-canon-agent-token.secret)" \
  -e FLYRADAR_MODEL="anthropic:claude-sonnet-4-6" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --link flycanon-api \
  -p 8580:8500 \
  ghcr.io/firefly-operationos/flyradar:${RADAR_TAG}


# ============================================================
# 6. Verify cross-service health
# ============================================================
curl -fsS http://localhost:8500/api/v1/version
curl -fsS http://localhost:8500/actuator/health/readiness
curl -fsS http://localhost:8580/api/v1/version
curl -fsS http://localhost:8580/actuator/health/readiness
```

Boot order is non-negotiable: flyradar's `FlycanonClient` bean is
constructed eagerly and the workspace cache subscriber begins
consuming on startup -- canon must be reachable.

For Kubernetes the equivalent is two `Job`s for migration, two
`Deployment`s + two `Service`s per service, and a `Secret` carrying
the minted agent token. The Job/Secret ordering matters but can be
managed by your release controller (Argo / Flux / plain `kubectl
apply` with `wait`).

For the user-tier mint flow's full DTO and the scope reference, see:

- [firefly-intelligence-system.md § Agent token model](firefly-intelligence-system.md#agent-token-model) -- model + scopes.
- flycanon: [consumers.md § Agent token surface](consumers.md#agent-token-surface) -- producer-side view.
- flyradar: [integration-with-flycanon.md § Setup: agent token mint](integration-with-flycanon.md#setup-agent-token-mint) -- consumer-side walkthrough.

---

## 7. Day-2 operations

Day-2 procedures (rotation, lifecycle, backups, rolling restarts)
live in the planned `operations-runbook.md`. Until that lands the
relevant material is split between:

- **Agent token rotation** -- flyradar: [integration-with-flycanon.md § Rotation](integration-with-flycanon.md). Mint a new token, set the env var on flyradar, restart radar, then revoke the old token on canon.
- **Workspace lifecycle** -- flycanon `POST /api/v1/workspaces`, `PATCH .../{id}`, `POST .../{id}:close`. Events emit on `canon.workspaces.v1`; flyradar's cache refreshes automatically.
- **Backups** -- canonical state lives in Postgres. A `pg_dump` (or your managed-provider snapshot) of each database is sufficient: `pyfly_eda_outbox`, `canon_*` / `flyradar_*` tables, and the audit trail are all in-band. See flycanon [deployment.md § Backup / DR](deployment.md#backup--dr).
- **Rolling restarts** -- both API + worker support `SIGTERM` drains (the `_WORKER_SHUTDOWN_GRACE_S` / `_WORKER_SHUTDOWN_TIMEOUT_S` envs control the cap). Kubernetes `terminationGracePeriodSeconds` must be at least the configured value.

---

## 8. Observability stack (recommended)

The services expose pyfly's standard actuator surface; the
recommended (out-of-tree) stack is:

| Concern | Tool | Wire-up |
|---------|------|---------|
| Metrics | Prometheus + Grafana | Scrape `GET /actuator/prometheus` on each pod's `:8500`. Pyfly emits the standard HTTP request totals + latency histograms, async-pool sizes, and the EDA outbox queue depth gauge. |
| Logs | Loki / ELK | Both services log structured JSON to stdout. Pyfly's `CorrelationFilter` stamps every log line with `correlation_id` (W3C trace-context aware) + `tenant_id` -- index those fields. |
| Tracing | Jaeger / Tempo | W3C `traceparent` / `tracestate` already propagate through both services and the cross-service handoff client. Spans are emitted to pyfly's observability stack; export to an external collector is an ops choice driven by the `pyfly.tracing` configuration. |
| Healthchecks | Kubernetes probes | `GET /actuator/health/readiness` aggregates DB + EDA publisher health; `/actuator/health/liveness` is a process-up signal. The flyradar worker also exposes a heartbeat file (`FLYRADAR_WORKER_HEARTBEAT_PATH`) for a Docker-side liveness check. |
| Dashboards | Grafana | Per-service: request volume / p95, EDA outbox depth, worker handler timeouts, LLM-call latencies (pyfly emits a `pyfly_llm_call_duration_seconds` histogram). |

---

## 9. Scaling decisions

Capacity-planning detail lives in the planned
`scale-and-performance.md`. Pending that doc, the rules of thumb that
hold today:

- **Scale flycanon-api by request QPS**, not data volume. Add one
  replica per ~200 sustained req/s on the heavy endpoints (`/search`,
  `/query`). Above that, the LLM-call wait time dominates and the
  Python event loop saturates.
- **Scale flycanon-worker by EDA-outbox queue depth**. Pyfly emits a
  `pyfly_eda_outbox_queue_depth` gauge. Alert at 1000 unprocessed
  entries; add workers until depth is consistently < 100.
- **Scale flyradar-worker by discovery-queue depth**. The
  `flyradar.jobs` topic carries one entry per submitted job; depth
  > 5 sustained means add a worker replica.
- **Move to Tier-B partitioning when a tenant's chunk count exceeds
  ~10M.** The default schema runs `canon_chunks` as a single partition;
  the Tier-B path (planned in `scale-and-performance.md`) shards by
  `(tenant_id)` so per-tenant indexes stay below the HNSW build
  budget. Until that lands, the practical ceiling per cluster is
  ~50M chunks total across all tenants -- past that point insert
  latency on `canon_chunk_vectors` rises into seconds.
- **Postgres read replicas help reporting / audit-tail queries**;
  they do not help the request path today (no read-replica routing
  yet). For request-path read scaling, run a larger primary.

---

## 10. Migration paths

The service contract is forward-compatible inside a major CalVer
year (`YY.*`). The cross-cutting changes you can safely plan for:

- **v1 (single-region, in-process EDA) -> v2 (multi-region, Kafka EDA).**
  Flip `FLYCANON_EDA_ADAPTER` and `FLYRADAR_EDA_ADAPTER` from
  `postgres` to `kafka`. Topics stay the same name; existing
  consumers (flyradar's workspace cache, downstream projections)
  reconnect by subscribing to the Kafka brokers instead of LISTEN /
  NOTIFY. Cut over per-topic to bound the blast radius -- start
  with the lowest-volume topic (`canon.workspaces.v1`).
- **In-process rate-limit -> Redis-backed rate-limit.** Today both
  services run a process-local sliding-window counter behind a
  `threading.Lock` for `rate_limit_rpm` enforcement on agent tokens.
  When the Redis-backed counter lands, the wire shape
  (`429 rate_limit_exceeded`) stays the same; the only config delta
  is a `_RATE_LIMIT_STORE=redis` selector and a `_REDIS_URL`. See
  each repo's `CHANGELOG.md` under "agent-token rate limiting".
- **In-process idempotency store -> Redis-backed.** Same pattern:
  the request side stays identical, only the storage selector flips.
  Planned for the next minor.
- **`FLYRADAR_APP_DATABASE_URL` split.** Today both services drive
  the request path through the admin role (the `after_begin` listener
  still issues `SET LOCAL app.tenant_id / app.workspace_id` so RLS
  policies fire, but they fire against an admin role with
  `BYPASSRLS`, which makes the policy a no-op in production). The
  planned `_APP_DATABASE_URL` split documented in
  `flyradar/env_template` turns the policy back on. To prepare:
  provision the `_app` role per section 3 now and keep the URL
  unset on the env until the code split lands.
