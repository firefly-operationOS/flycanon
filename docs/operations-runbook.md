# Operations runbook

> Audience: on-call engineer responding to a flycanon incident, or an
> SRE wiring up monitoring + probes for a new deployment.
>
> This document is problem-first. The wire contract is in
> [api-reference.md](api-reference.md); deployment topology is in
> [deployment.md](deployment.md); architecture is in
> [architecture.md](architecture.md); the consumer-facing contract is
> in [consumers.md](consumers.md). Generic problem-cause-fix tables
> covering ingestion, embeddings, retrieval, and the answer model are
> in [troubleshooting.md](troubleshooting.md); the runbook focuses on
> operational primitives (probes, RLS, token rotation, backup,
> upgrade, DR).

---

## 1. Health + readiness checks

flycanon exposes four probe endpoints. Use them in this order when
triaging a sick replica.

| Endpoint | Purpose | Liveness/Readiness |
|---|---|---|
| `GET /api/v1/version` | App booted, controllers wired, OpenAPI generated. Does NOT require tenant headers. | Smoke test for a fresh deploy; safe for an unauthenticated probe. |
| `GET /actuator/health/liveness` | Process is up and the event loop is not hung. Always 200 unless the runtime is wedged. | Wire as the Kubernetes `livenessProbe`. |
| `GET /actuator/health/readiness` | Aggregate of `database_health` + `eda_health`. 503 while Postgres or the broker is unreachable. | Wire as the Kubernetes `readinessProbe`. |
| `GET /actuator/health` | Aggregated dashboard (every registered indicator). | Operator console only. |
| `GET /actuator/metrics` | Prometheus scrape surface. | Scrape from Prometheus on the cluster. |

Reference Kubernetes wiring (matches
[deployment.md § Observability](deployment.md#observability)):

```yaml
livenessProbe:
  httpGet:
    path: /actuator/health/liveness
    port: 8500
  initialDelaySeconds: 10
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /actuator/health/readiness
    port: 8500
  initialDelaySeconds: 5
  periodSeconds: 5
```

The compose stack in [`docker-compose.yml`](../docker-compose.yml)
hits the readiness endpoint via a `curl --fail` healthcheck; mirror
the same period on Kubernetes. The `worker` role has no HTTP server
-- its health signal is "container alive + consuming from the EDA
outbox / broker".

---

## 2. Common failures + diagnostics

Symptom-first. Skim the left column, find the row that matches what
the caller is seeing, follow the diagnostic, then the fix. Generic
ingestion / retrieval failures are in
[troubleshooting.md](troubleshooting.md); the table below covers the
multitenancy + agent surface that the runbook owns.

| Symptom | Likely cause | Diagnostic | Fix |
|---|---|---|---|
| Every read under the non-superuser app role returns 0 rows; admin role sees data fine | `TenantContextMiddleware` is not registered, so the ContextVar is never bound and the `after_begin` listener can't issue `SET LOCAL`. The "RLS theatre" symptom. | Tail Postgres logs for transactions with empty `current_setting('app.tenant_id', true)`. Confirm `TenantContextMiddleware` is registered (`grep -r TenantContextMiddleware src/flycanon/main.py`). | The middleware ships in [`src/flycanon/web/conventions/middleware.py`](../src/flycanon/web/conventions/middleware.py) and is wired at app construction time. Re-register it; redeploy. |
| Caller from workspace X presents a resource id from workspace Y and gets `404 resource_not_found` | Legitimate workspace-scope enforcement, not a bug. Documented in [api-reference.md § Workspace scope enforcement](api-reference.md#workspace-scope-enforcement). | Verify the caller's `X-Workspace-Id` matches the workspace the resource was minted under. | Reroute the caller to the correct workspace. |
| Same `Idempotency-Key` retried -- second response identical to the first, no new source / candidate / query created | Expected. The agent surface mandates `Idempotency-Key` so duplicate machine calls dedup; the replay returns the cached response. | Look in `canon_audit_events` for the original key. A distinct payload with the same key returns `409 idempotency_key_conflict`. | None. This is the contract. If the caller wants a fresh write, send a new key. |
| Agent calls return `403 invalid_agent_token` after a recent rotation | Token was revoked, or a consumer replica is holding the stale env var. | `GET /api/v1/agent-tokens` to confirm the token row exists and `revoked_at IS NULL`. Check the consumer's `env` for its agent-token env var. | Roll the consumer replicas onto the new secret; revoke only after universal adoption. See [§ 3 Token lifecycle ops](#3-token-lifecycle-ops). |
| Agent calls return `403 agent_token_expired` | `expires_at` on the token has passed. | `GET /api/v1/agent-tokens` and check `expires_at`. | Mint a new token; update the consumer's env var; restart. |
| Agent calls return `429 rate_limit_exceeded` | Token has exhausted its per-minute budget. Configured via `rate_limit_rpm` at mint time; enforced as a process-local sliding 60s window keyed by `token_id`. | `GET /api/v1/agent-tokens` and check `rate_limit_rpm`. The CHANGELOG flags this as in-memory + process-local. | Slow the consumer; if legitimate, re-mint the token with a higher `rate_limit_rpm` and revoke the old. The limiter is per-process today -- multi-replica deploys multiply the effective rate (see [security-model.md § 6](security-model.md#6-rate-limiting)). |
| Workspace cache stale on a downstream service | EDA broker outage; subscriber not attached on the consumer side; topic mismatch (`FLYCANON_WORKSPACE_TOPIC` vs the consumer's setting). | Check `pyfly_eda_outbox` for unpublished rows: `SELECT topic, count(*) FROM pyfly_eda_outbox WHERE published_at IS NULL GROUP BY 1`. | Restart the worker (it pumps the outbox); confirm `LISTEN/NOTIFY` is not blocked by pg_bouncer in transaction mode. See [troubleshooting.md § Subscribers aren't receiving events](troubleshooting.md#subscribers-arent-receiving-flycanonknowledge-events). |
| `canon_chunk_vectors` writes from a non-admin role fail with `new row violates row-level security policy` on a freshly deployed cluster | Deploy-ordering bug: migration `0013_rls_policies` runs before `PgvectorStore` boots, so the runtime-created table briefly exists without RLS. On reboot the PgvectorStore bootstrap installs the policy. | Confirm `pg_policies` has a row for `canon_chunk_vectors` with `policyname = 'tenant_workspace_isolation'`. | Restart the API container; `PgvectorStore._initialise_schema` installs the RLS policy idempotently on the next boot (see [§ 10 `canon_chunk_vectors` deploy ordering](#10-canon_chunk_vectors-deploy-ordering)). |
| pgvector HNSW queries are very slow | HNSW index missing, or `hnsw.ef_search` set higher than necessary, or `m` / `ef_construction` raised past the defaults. | `\di+ canon_chunk_vectors_hnsw` against Postgres. Inspect `FLYCANON_PGVECTOR_HNSW_M` / `_HNSW_EF_CONSTRUCTION`. | The defaults (m=16, ef_construction=64) are correct for most corpora. If you bumped them past 200, reindex. See [troubleshooting.md § High p99 on `/api/v1/search`](troubleshooting.md#high-p99-on-apiv1search). |
| `alembic upgrade head` fails with `current revision in db doesn't match` | Migration drift -- DB has been hand-patched, or the deploy ran against a DB that's already on `head`. | `alembic current` against the live DB. Compare against the highest revision in `migrations/versions/` (currently `0013_rls_policies`). | Reconcile by `alembic stamp head` ONLY when the DB matches the schema; otherwise hand-resolve by running missing revisions individually. |
| `relation "canon_sources" does not exist` on first boot | Schema not on the database yet. | `alembic current` returns empty. | Run migrations once: `docker run --rm --env-file .env ghcr.io/firefly-operationos/flycanon:latest migrate`. Detail in [troubleshooting.md § Service boot](troubleshooting.md#service-boot). |

---

## 3. Token lifecycle ops

The agent-token CRUD is documented in
[api-reference.md § Token management](api-reference.md#token-management).
Day-2 commands an operator runs:

### Mint a new agent token

```bash
curl -X POST https://canon.example.com/api/v1/agent-tokens \
  -H "Authorization: Bearer $OPERATOR_JWT" \
  -H "X-Tenant-Id: acme" \
  -H "X-Workspace-Id: ws-prod" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "flyradar-prod-2026q2",
    "scopes": [
      "agent.sources:ingest",
      "agent.sources:read"
    ],
    "workspace_allowlist": ["ws-prod"],
    "rate_limit_rpm": 60,
    "expires_at": "2026-08-22T00:00:00Z"
  }'
```

The response (`AgentTokenCreated`) is the only time the raw secret
crosses the wire -- capture it on the response; flycanon persists
only its SHA-256 hash. Recommendations on scope, allowlist, and
expiry: [consumers.md § Agent token surface](consumers.md#agent-token-surface).

### List active tokens

```bash
curl https://canon.example.com/api/v1/agent-tokens \
  -H "Authorization: Bearer $OPERATOR_JWT" \
  -H "X-Tenant-Id: acme" \
  -H "X-Workspace-Id: ws-prod"
```

Returns `AgentTokenSummaryDto[]` -- prefix + metadata only, never
the secret. `last_used_at` flags dormant tokens safe to revoke;
`expires_at` flags upcoming rotations.

### Revoke a token

```bash
curl -X DELETE \
  "https://canon.example.com/api/v1/agent-tokens/${TOKEN_ID}" \
  -H "Authorization: Bearer $OPERATOR_JWT" \
  -H "X-Tenant-Id: acme" \
  -H "X-Workspace-Id: ws-prod"
```

204 on success. Already-revoked tokens are a no-op (still 204).
Unknown `token_id` returns `404 resource_not_found`. Revocation is
immediate -- subsequent calls fail with `invalid_agent_token`.

### Rotate without downtime

The procedure documented in [consumers.md § Rotation procedure](consumers.md#rotation-procedure-producer-view):

1. Mint the new token under the same tenant + scopes.
2. Consumer updates its env / secret store; rolls its replicas onto
   the new secret.
3. Revoke the old token (idempotent).

`last_used_at` confirms the cutover landed before you revoke.

---

## 4. Workspace lifecycle ops

flycanon is the **canonical store** for workspace identity. The CRUD
surface is at `/api/v1/workspaces` (see
[api-reference.md § Workspaces](api-reference.md#workspaces)). Every
mutation emits an event on `canon.workspaces.v1` so consumers
(notably flyradar's workspace cache) refresh without polling.

### Create a workspace

```bash
curl -X POST https://canon.example.com/api/v1/workspaces \
  -H "Authorization: Bearer $OPERATOR_JWT" \
  -H "X-Tenant-Id: acme" \
  -H "X-Workspace-Id: ws-prod" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "ws-prod",
    "name": "Production engagement",
    "scope": "...",
    "sme_roster": [],
    "retention_days": 365,
    "jurisdiction": "EU"
  }'
```

Emits `WorkspaceCreated` on `canon.workspaces.v1`. Consumers
subscribed to the topic populate their cache row directly from the
event payload (no HTTP fetch required).

### Close a workspace (soft delete)

```bash
curl -X POST \
  "https://canon.example.com/api/v1/workspaces/${WORKSPACE_ID}:close" \
  -H "Authorization: Bearer $OPERATOR_JWT" \
  -H "X-Tenant-Id: acme" \
  -H "X-Workspace-Id: ${WORKSPACE_ID}"
```

The route is idempotent -- closing an already-closed workspace is a
no-op (`status` already `closed`, `closed_at` already set). Emits
`WorkspaceDeleted` on `canon.workspaces.v1`; consumers evict their
cache entry on receipt. flycanon has no hard-delete -- the row stays
in `canon_workspaces` with `status=closed` for audit.

### Verify cache invalidation

After a close, the consumer-side cache row should be evicted within
one EDA event hop. If a consumer is still serving stale data:

- Confirm the consumer subscribed (their startup log will show a
  `workspace_event_subscriber_attached` line).
- Confirm `pyfly_eda_outbox` is draining (no unpublished rows for
  the workspace topic).
- Restart the consumer to force a lazy fill from
  `GET /api/v1/workspaces/{id}`.

---

## 5. RLS role provisioning

flycanon's RLS model is documented in
[architecture.md § Row-level security](architecture.md#row-level-security)
and the role split in
[deployment.md § RLS roles](deployment.md#rls-roles). Migration
[`0013_rls_policies`](../migrations/versions/20260522_1500_0013_rls_policies.py)
emits `ALTER TABLE ... FORCE ROW LEVEL SECURITY` on every `canon_*`
table. The application MUST NOT run as a Postgres superuser, and
migrations + workers MUST have `BYPASSRLS`.

Provision two roles before pointing `FLYCANON_DATABASE_URL` at the
cluster:

```sql
-- Admin role: migrations + cross-workspace workers (consolidation
-- re-embed sweep, retention reaper, EDA ingest worker).
CREATE ROLE flycanon_admin LOGIN PASSWORD 'change-me' BYPASSRLS;
GRANT ALL PRIVILEGES ON DATABASE flycanon TO flycanon_admin;
GRANT ALL ON SCHEMA public TO flycanon_admin;

-- App role: request-path engine. NO BYPASSRLS, NOT a superuser.
-- RLS policies filter by the per-session GUCs the after_begin
-- listener (install_tenant_guc_hook) sets on each transaction.
CREATE ROLE flycanon_app LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE flycanon              TO flycanon_app;
GRANT USAGE   ON SCHEMA public                  TO flycanon_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES    IN SCHEMA public             TO flycanon_app;
GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public             TO flycanon_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLES                                   TO flycanon_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT
    ON SEQUENCES                                TO flycanon_app;
```

Wiring:

- `FLYCANON_DATABASE_URL` at `flycanon_admin` for the `migrate` +
  `worker` commands.
- `FLYCANON_DATABASE_URL` at `flycanon_app` for the `serve` command.

The integration suite (`tests/integration/test_rls_isolation.py`)
pins the `BYPASSRLS` vs `app_user` contract end-to-end. The
parallel role provisioning on flyradar is documented in
[flyradar/docs/operations-runbook.md § RLS role provisioning](../../flyradar/docs/operations-runbook.md#5-rls-role-provisioning).

---

## 6. Observability hooks

flycanon emits structured logs + Prometheus metrics today. Tracing
is best-effort via pyfly's `CorrelationFilter`.

| Signal | Where it lives | How to consume |
|---|---|---|
| Structured logs | stdout; one event per line. Pyfly's `CorrelationFilter` decorates every log with `traceparent`, `tracestate`, `X-Correlation-Id`, `X-Request-Id`, `X-Tenant-Id`. | Pipe stdout to your log aggregator (Loki, Datadog, CloudWatch). Filter on `correlation_id` to stitch a single request across replicas + cross-service handoffs. |
| Prometheus metrics | `GET /actuator/metrics`. Pyfly emits the standard HTTP request count + latency, async pool sizes, runtime gauges. | Scrape from Prometheus; build dashboards on request count, p99 latency, 4xx/5xx breakdown by `code`. |
| Audit log | `canon_audit_events`. Every mutation (sources, knowledge versions, candidates, taxonomy, workspaces, agent verifies) lands one row with `correlation_id`, `actor`, `subject_kind`, `subject_id`, `occurred_at`, `payload`. | Query Postgres directly with `(tenant_id, workspace_id)` filters; or consume the `flycanon.audit` EDA topic for a near-realtime stream. |
| Cost trail | `canon_cost_events`. Per-LLM-call cost rollup with `actor`, `correlation_id`, model, latency, prompt + completion tokens. | Drives the `/api/v1/billing/*` endpoints. Query directly for forensics. |

Recommended Prometheus alerts:

| Alert | Threshold | Why |
|---|---|---|
| Sustained 5xx rate | > 1% over 5 m | A burst points at Postgres / EDA broker unreachability, or an embedding-provider outage. |
| p99 latency on `POST /api/v1/agent/sources` | > 15 s | The slowest sync path on the agent surface (large PDF + OCR). A spike beyond this means the OCR engine is overloaded or the embedding provider is rate-limiting. |
| `pyfly_eda_outbox` unpublished count | > 100 | Worker not draining; subscribers will start running stale. |
| Discovery (or ingest) worker heartbeat staleness | > 2x grace | Worker stuck or crashed; queue draining stops. |
| RLS rejection logs (`InsufficientPrivilege: new row violates row-level security policy`) | any | A handler tried to write into a foreign workspace; investigate the controller. |
| Idempotency replay rate | trend | A surge suggests an aggressive caller; inspect their backoff. |
| Embedding provider 429 rate | trend | flycanon's intake will block until the provider recovers. Adjust `FLYCANON_EMBEDDING_MAX_CONCURRENT_REQUESTS`. |

OpenTelemetry tracing is best-effort -- pyfly emits spans when an
OTLP endpoint is configured. The integration-points where tracing
adds most value: the retrieval path (BM25 + vector + RRF fusion),
the consolidation pipeline (LLM call + candidate write), and the
audit-event publish.

---

## 7. Backup + restore

flycanon's durable state lives in **Postgres**. The vector
projection is co-located in the same Postgres (the
`canon_chunk_vectors` table) when `FLYCANON_VECTOR_STORE=pgvector`
(the default); a standard `pg_dump` captures both the canonical
store and the vector projection in one snapshot.

What's in the backup (from [deployment.md § Backup / DR](deployment.md#backup--dr)):

- `canon_sources` (no bytes -- only metadata + extracted text refs).
- `canon_chunks` (text + section path + page).
- `canon_chunk_vectors` (embeddings) -- when vector store is
  `pgvector`.
- `canon_knowledge_items` + `canon_knowledge_versions` +
  `canon_citations`.
- `canon_candidates` + `canon_audit_events` + `canon_taxonomy_nodes`.
- `canon_workspaces` + `canon_agent_tokens` + `canon_conversations` +
  `canon_conversation_turns`.
- `canon_cost_events`.
- `pyfly_eda_outbox` -- for in-flight EDA messages on the postgres
  adapter.

Special considerations:

- **Agent tokens are not recoverable if rotated.** Restoring a
  backup that contains a token which has since been revoked /
  rotated does NOT restore the working secret -- the secret is only
  in the original mint response, never in flycanon's storage. Mint
  new tokens after a restore + propagate to consumers.
- **Vector projection** lives in `pgvector` in the same Postgres by
  default (`FLYCANON_VECTOR_STORE=pgvector`), so a single `pg_dump`
  captures it -- there is no separate vector store to back up. With an
  external dense backend (`qdrant` / `chroma`) the projection lives
  outside Postgres and must be backed up separately -- though it is a
  DERIVED projection and can always be rebuilt by re-indexing from
  `canon_chunks`, so a Postgres-only backup remains sufficient for
  recovery.
- **BM25 projection** lives in `canon_chunks.tsv` (a Postgres
  GENERATED column with a GIN index, per migration `0003_bm25_tsv`);
  a `pg_dump` captures it too. There is no separate corpus to back
  up.

Recommended cadence: hourly snapshots for the cost trail + audit
log; daily full dumps for everything else. Pair with cross-region
replication for production.

---

## 8. Upgrade procedure

flycanon releases are CalVer (`YY.MM.PP`) per the compatibility
contract in [deployment.md § Upgrades](deployment.md#upgrades) and
[CHANGELOG.md](../CHANGELOG.md):

- **`YY.MM.x`** -- drop-in patches; no schema, env var, or SDK
  breakage.
- **`YY.MM.0`** -- monthly. May add optional env vars or new
  endpoints, never break existing ones. Run `migrate` before the
  new image rolls out.
- **Major (year)** -- explicitly documented breakers.

Rolling upgrade procedure:

```bash
# 1. Pull the new image.
docker pull ghcr.io/firefly-operationos/flycanon:26.5.6

# 2. Run migrations against the live database (admin role).
docker run --rm \
  --env FLYCANON_DATABASE_URL=$ADMIN_DATABASE_URL \
  ghcr.io/firefly-operationos/flycanon:26.5.6 migrate

# 3. Roll the API + worker replicas.
docker compose --env-file .env up -d
```

### Service ordering

When upgrading both flycanon and flyradar in lockstep, **upgrade
flycanon first**, then flyradar. flycanon is the canonical store for
workspace identity and the producer of `canon.workspaces.v1`; an
older flyradar can consume a newer flycanon (the wire contract is
append-only), but a newer flyradar against an older flycanon is the
direction supported for staged rollouts.

### Pre-upgrade checklist

- Review the CHANGELOG entry for the target version.
- Confirm `FLYCANON_DATABASE_URL` for the migration job points at
  the `flycanon_admin` (`BYPASSRLS`) role.
- Confirm the application role connection (`flycanon_app`) is
  unchanged.
- Set `RUN_MIGRATIONS=false` on long-running containers; run the
  one-shot `migrate` Job / Container first.
- If the new release introduces a new RLS policy, the `flycanon_admin`
  role is required to apply it -- migrating as `flycanon_app` will
  fail with `insufficient_privilege`.

---

## 9. Disaster recovery

| Scenario | flycanon behaviour | Operator action |
|---|---|---|
| Postgres outage | Fail-fast: `/actuator/health/readiness` returns 503, requests fail with 5xx. No graceful degradation -- the canonical state IS Postgres. | Restore Postgres; the worker re-claims its queue and replays from the durable outbox. |
| EDA broker outage (postgres adapter) | The Postgres outbox keeps the events durable. Subscribers stop receiving fresh events but `pyfly_eda_outbox` accumulates. | Restore the broker / restart the `flycanon-worker` container; the worker pumps the outbox as soon as connectivity returns. |
| EDA broker outage (redis adapter) | Redis Streams without `--appendonly yes` lose events on Redis restart. | Redeploy Redis with persistence; use the `postgres` adapter (default) for durability. See [troubleshooting.md § `FLYCANON_EDA_ADAPTER=redis`](troubleshooting.md#flycanon_eda_adapterredis----events-silently-dropped). |
| Embedding provider outage | `POST /api/v1/sources` returns `502 answer_model_failed` (or a similar `provider_*` code) once the retry budget is exhausted. The canonical row is rolled back; the source is not committed half-indexed. | Switch to a fallback provider via `FLYCANON_EMBEDDING_MODEL`; configure `FLYCANON_ANSWER_FALLBACK_MODEL`; re-ingest the affected sources after recovery. |
| Region failover | flycanon is stateless apart from Postgres + the broker. Stand up replicas in the failover region pointing at the replicated Postgres + broker. | Cross-region replication of the durable state is the contract; flycanon replicas spin up cleanly against a hot standby. |
| Catastrophic data loss | Restore Postgres from the latest backup; mint new agent tokens for every consumer (old tokens are only valid if rotation didn't touch them since the backup). | Run `alembic upgrade head` against the restored DB before serving traffic. |

---

## 10. `canon_chunk_vectors` deploy ordering

**flycanon-specific.** The pgvector projection table
(`canon_chunk_vectors`) is created at runtime by
[`PgVectorVectorStore._initialise_schema()`](../src/flycanon/core/services/retrieval/pgvector_store.py)
on first boot, not by Alembic. Migration
[`0013_rls_policies`](../migrations/versions/20260522_1500_0013_rls_policies.py)
guards the RLS policy with `IF EXISTS` because the table may not
exist when the migration runs:

```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'canon_chunk_vectors'
    ) THEN
        EXECUTE 'ALTER TABLE canon_chunk_vectors ENABLE ROW LEVEL SECURITY';
        ...
    END IF;
END
$$;
```

On a **first deploy** the sequence is:

1. Empty database.
2. `alembic upgrade head` runs as `flycanon_admin`. Migration `0013`
   no-ops on the `canon_chunk_vectors` ALTER (table doesn't exist
   yet).
3. API container boots; `PgVectorVectorStore` creates the table at
   first index call.

Between steps 2 and 3, the table briefly exists without RLS.
`PgVectorVectorStore._initialise_schema` mitigates this by installing
the RLS policy in-band with table creation -- a DO block that's
idempotent (skips if the policy already exists) and soft-fails on
`insufficient_privilege` so a non-admin boot logs a warning instead
of crashing.

**Operational checklist after a first deploy:**

```sql
-- Confirm the policy was installed.
SELECT polname FROM pg_policies
WHERE tablename = 'canon_chunk_vectors';
-- Expect: tenant_workspace_isolation
```

If the policy is missing (insufficient_privilege at boot), restart
the API container with `FLYCANON_DATABASE_URL` pointing at the admin
role for the one boot; `_initialise_schema` installs the policy then
revert.

---

## 11. Cross-references

- [architecture.md](architecture.md) -- data model, RLS, workspace
  lifecycle.
- [api-reference.md](api-reference.md) -- the full REST surface +
  error catalogue.
- [deployment.md](deployment.md) -- environment variables, RLS role
  provisioning, embedding providers, OCR engines.
- [consumers.md](consumers.md) -- consumer-side contract for
  downstream services.
- [troubleshooting.md](troubleshooting.md) -- problem -> cause ->
  fix index for ingestion / embeddings / retrieval / answer model /
  EDA failures.
- [security-model.md](security-model.md) -- the multitenancy +
  agent-surface threat model.
- [CHANGELOG.md](../CHANGELOG.md) -- per-release breaking-change
  notes.
- [flyradar/docs/operations-runbook.md](../../flyradar/docs/operations-runbook.md)
  -- the matching runbook on the flyradar side.
