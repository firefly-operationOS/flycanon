# Security model

> Audience: security reviewer auditing flycanon's multitenancy
> boundary, agent surface, and operational risk profile.
>
> Architecture and the data model are in
> [architecture.md](architecture.md); the request-path API is in
> [api-reference.md](api-reference.md); the consumer-side contract
> for downstream services is in [consumers.md](consumers.md); the
> EDA event catalogue is in [eda-events.md](eda-events.md). This
> document focuses on what flycanon treats as adversarial, what
> defenses it deploys, and what is intentionally out of scope.

---

## 1. Threat model

flycanon is designed against four classes of adversary.

| Adversary | Capability | flycanon response |
|---|---|---|
| **External attacker, no credentials** | Hits any `/api/v1/*` endpoint without auth headers. | 401 / 403 at the first gate. The only unauthenticated route is `GET /api/v1/version` (and the `/actuator/health/*` probes). Every other path requires tenant headers + JWT (when `FLYCANON_API_KEYS` is set or `pyfly.security.oauth2.resource-server.enabled=true`) or `X-Agent-Token`. |
| **Authenticated user in tenant A, trying to reach tenant B** | Holds a valid JWT for tenant A; hand-crafts a request with `X-Tenant-Id: B`. | `403 tenant_claim_mismatch` from the conventions layer (the JWT `tenant_id` claim is verified against `X-Tenant-Id`). Even if that gate were bypassed, Postgres RLS returns zero rows. |
| **Authenticated user in workspace X, trying to reach workspace Y in same tenant** | Holds a valid JWT for tenant T; hand-crafts a request with `X-Workspace-Id: Y` while referencing a resource id from workspace X. | `404 resource_not_found` (workspace scope enforced on every read-by-id route; documented in [api-reference.md § Workspace scope enforcement](api-reference.md#workspace-scope-enforcement)). The repository WHERE clause and Postgres RLS each independently produce the 404. |
| **Holder of a compromised agent token** | Has the raw secret; can present `X-Agent-Token` against any agent route until the token is revoked. | Revoke via `DELETE /api/v1/agent-tokens/{id}` (see [operations-runbook.md § Token lifecycle ops](operations-runbook.md#3-token-lifecycle-ops)). After revoke, the next `verify` call fails with `invalid_agent_token`. Blast radius is bounded by the token's `workspace_allowlist` + `scopes`. |

Explicitly **out of scope**:

- **Compromise of a `BYPASSRLS` Postgres role.** A role with
  `BYPASSRLS` (the migration runner, the worker, consolidation
  re-embed sweep) sees every workspace's data by Postgres design.
  This is a deployment trust boundary, not a flycanon invariant.
- **Compromise of the Postgres superuser.** `FORCE ROW LEVEL
  SECURITY` does not apply to superusers (a Postgres rule); the
  application MUST NOT connect as a superuser.
- **Operator with operator JWT minting tokens for adversarial
  purposes.** Token minting requires a user-tier JWT; this is the
  same trust boundary as the JWT issuer (your IdP).
- **Adversarial source content.** flycanon's binary normaliser
  enforces caps (`FLYCANON_BINARY_MAX_RECURSION_DEPTH`,
  `FLYCANON_BINARY_MAX_EXPANDED_FILES`,
  `FLYCANON_MAX_BYTES`) on archives + uploads to defeat zip-bomb
  style fan-out, but content-level adversarial inputs (prompt
  injection, embedded malicious URLs in ingested PDFs) are caller
  responsibility -- flycanon stores text, not bytes, and does not
  execute content.

---

## 2. Tenant + workspace isolation

flycanon enforces tenant + workspace isolation through a
five-layer defense. Any single layer failing degrades to a less
strict but still safe posture; all five must fail simultaneously for
data to leak.

1. **JWT validation (user tier) / agent token verification (agent
   tier).** The first gate. The conventions layer in
   [`web/conventions/deps.py`](../src/flycanon/web/conventions/deps.py)
   parses `Authorization: Bearer <jwt>` and matches the JWT
   `tenant_id` claim against `X-Tenant-Id`. A mismatch is
   `403 tenant_claim_mismatch`. The agent surface verifies the
   token per-request against `canon_agent_tokens` (see [§ 3 Agent
   token security](#3-agent-token-security)).
2. **Header validation (`X-Tenant-Id`, `X-Workspace-Id` slug
   regex).** Both headers must match
   `^[a-z0-9][a-z0-9_-]{0,63}$`. Anything else returns
   `400 missing_tenant_context`. The slug grammar is the security
   boundary that lets the GUC layer safely string-format the value
   into `SET LOCAL` -- documented in
   [`web/conventions/db.py`](../src/flycanon/web/conventions/db.py).
3. **Service-layer scope enforcement.** Handlers explicitly thread
   `(tenant_id, workspace_id)` into every service call;
   `RetrievalService.search()` raises `MissingTenantContext` when
   scope is missing -- fail-closed.
4. **Repository WHERE clauses.** Every read filters by
   `(tenant_id, workspace_id)` (or `tenant_id` only for the
   tenant-scoped `canon_agent_tokens`). Defense in depth -- the
   next layer catches what this misses.
5. **Postgres RLS policies via `SET LOCAL` GUCs.** Migration
   [`0013_rls_policies`](../migrations/versions/20260522_1500_0013_rls_policies.py)
   emits `ALTER TABLE ... FORCE ROW LEVEL SECURITY` on every table
   plus a `USING` policy that matches `tenant_id` (and
   `workspace_id` where applicable) against per-session GUCs. The
   GUC plumbing is in
   [`web/conventions/db.py`](../src/flycanon/web/conventions/db.py)
   -- `install_tenant_guc_hook()` registers a SQLAlchemy
   `after_begin` listener that reads the request-scoped
   `TenantContext` ContextVar and issues `SET LOCAL` on every
   transaction.

The `TenantContextMiddleware` in
[`web/conventions/middleware.py`](../src/flycanon/web/conventions/middleware.py)
binds the ContextVar from request headers BEFORE the route runs so
the GUC listener has scope to apply on the first DB session. Without
the middleware (e.g., it gets unregistered), pyfly's
`@rest_controller` parameter resolver bypasses FastAPI's `Depends`
chain and the GUCs are never set; in production the
non-`BYPASSRLS` role then returns zero rows from every read -- the
documented "RLS theatre" symptom.

---

## 3. Agent token security

flycanon's agent token surface is implemented in
[`agent_token_service.py`](../src/flycanon/core/services/auth/agent_token_service.py).
The salient properties:

- **Format.** `agt_<8hex>_<32hex>`. The first 12 chars
  (`agt_<8hex>`) are the public prefix used as the database lookup
  key. The trailing 32 hex chars are the secret -- **128 bits of
  cryptographic entropy** from `secrets.token_hex(16)`.
- **Persistence.** Only the SHA-256 hash of the full token is
  stored, in `canon_agent_tokens.secret_hash`. The raw token is
  returned in the mint response exactly once
  (`AgentTokenCreated.token`) and never round-tripped on any other
  endpoint. The listing endpoint exposes only the public 12-char
  `prefix`.
- **Constant-time hash equality.** Verification uses
  `secrets.compare_digest(row.secret_hash, sha256(token))` to defeat
  timing-side-channel probes that would otherwise learn the hash one
  byte at a time.
- **`last_used_at` dedup to 60 s.** The verifier only updates
  `last_used_at` if the previous update is more than 60 s old. This
  rate-limits the information leak a frequent caller could otherwise
  use to infer token activity from row mtime.
- **Workspace allowlist.** A non-null
  `workspace_allowlist_json` restricts the token to the listed
  workspace ids. A null allowlist means the token works for any
  workspace under the tenant. Cross-allowlist calls return
  `403 agent_workspace_not_in_allowlist`.
- **Scope enforcement.** Every agent route declares a `scope`
  string (`agent.sources:ingest`, `agent.query:run`,
  `agent.knowledge:read`, `agent.candidates:propose`); the verifier
  rejects the token with `403 agent_scope_denied` if the scope is
  not in `scopes_json` (and the wildcard `*` is absent).
  Documented in
  [api-reference.md § Scope strings](api-reference.md#scope-strings).
- **Tenant scoping at the SQL layer.** The repository's
  `get_by_prefix(prefix, tenant_id=...)` filters by both prefix and
  tenant. A row with the right prefix but wrong tenant returns
  `None`, producing `InvalidAgentToken("Unknown agent token.")` --
  the tenant-mismatch distinction is not leaked.
- **Per-token rate limiter.** A sliding 60 s window keyed by
  `token_id` enforces `rate_limit_rpm` at verify time. Process-local
  today (see [§ 6 Rate limiting](#6-rate-limiting)).

### Defense-in-depth on revoke

`AgentTokenService.revoke(token_id, tenant_id=...)` is the only
revoke surface. `tenant_id` is REQUIRED and forwarded to the
repository's WHERE clause -- a caller from tenant A can never revoke
a token belonging to tenant B even if they hand-craft the
`token_id`. Postgres RLS on `canon_agent_tokens` is the second gate
(see [§ 4 RLS policies](#4-rls-policies)).

### Mutual exclusion with JWT

The `X-Agent-Token` header is mutually exclusive with
`Authorization: Bearer <jwt>` -- presenting both is rejected at the
conventions layer. This prevents a caller from blending agent and
user credentials to compose unintended scope.

### Agent-tier callers cannot mint

A caller whose `ctx.actor` begins with `agent:` is refused from the
`/api/v1/agent-tokens` CRUD with `403 agent_cannot_mint`. Agents
cannot fan out new credentials for other agents.

---

## 4. RLS policies

Migration
[`0013_rls_policies`](../migrations/versions/20260522_1500_0013_rls_policies.py)
enables Postgres row-level security on every `canon_*` table. 16
tables in total; three policy shapes.

### Standard `(tenant_id, workspace_id)` policy

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_workspace_isolation ON <table>
  USING (
    tenant_id    = current_setting('app.tenant_id',    true)
    AND workspace_id = current_setting('app.workspace_id', true)
  );
```

Applied to the 14 scoped tables:

- `canon_audit_events`
- `canon_candidates`
- `canon_chunks`
- `canon_citations`
- `canon_conversation_turns`
- `canon_conversations`
- `canon_cost_events`
- `canon_ingest_job_events`
- `canon_ingest_jobs`
- `canon_knowledge_items`
- `canon_knowledge_relations`
- `canon_knowledge_versions`
- `canon_sources`
- `canon_taxonomy_nodes`

### Special-case: `canon_workspaces`

```sql
CREATE POLICY tenant_workspace_isolation ON canon_workspaces
  USING (
    tenant_id = current_setting('app.tenant_id', true)
    AND id    = current_setting('app.workspace_id', true)
  );
```

The `id` column **is** the workspace identity, so the policy matches
`tenant_id` AND `id = app.workspace_id`. The workspace controller's
`LIST` path runs with `BYPASSRLS` (per-tenant listing across all
workspaces of the tenant).

### Special-case: `canon_agent_tokens` (tenant-only)

```sql
CREATE POLICY tenant_isolation ON canon_agent_tokens
  USING (
    tenant_id = current_setting('app.tenant_id', true)
  );
```

`canon_agent_tokens` rows can serve multiple workspaces via the
token's `workspace_allowlist_json`, so a workspace-scoped policy
would hide legitimate rows.

### Special-case: `canon_chunk_vectors` (runtime-created)

The pgvector projection table is created at boot by
[`PgVectorVectorStore`](../src/flycanon/core/services/retrieval/pgvector_store.py),
not by Alembic. Migration `0013` guards with `IF EXISTS`; the
PgvectorStore bootstrap installs the same RLS policy in-band with
table creation:

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'canon_chunk_vectors'
          AND policyname = 'tenant_workspace_isolation'
    ) THEN
        BEGIN
            EXECUTE 'ALTER TABLE canon_chunk_vectors ENABLE ROW LEVEL SECURITY';
            EXECUTE 'ALTER TABLE canon_chunk_vectors FORCE ROW LEVEL SECURITY';
            EXECUTE $POLICY$
                CREATE POLICY tenant_workspace_isolation ON canon_chunk_vectors
                  USING (
                    tenant_id = current_setting('app.tenant_id', true)
                    AND workspace_id = current_setting('app.workspace_id', true)
                  )
            $POLICY$;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE WARNING ...;
        END;
    END IF;
END
$$;
```

The DO block is idempotent (skips if the policy already exists) and
soft-fails on `insufficient_privilege` so a non-admin boot logs a
warning instead of crashing. The deploy-ordering gap is documented
in [operations-runbook.md § `canon_chunk_vectors` deploy ordering](operations-runbook.md#10-canon_chunk_vectors-deploy-ordering).

### Write-path enforcement

The `USING`-only policies the migration creates auto-derive `WITH
CHECK = USING` (Postgres docs: "If a `WITH CHECK` expression is not
specified, then it is the same as `USING` expression."). Cross-scope
**INSERTs** are blocked -- psycopg raises `InsufficientPrivilege`
("new row violates row-level security policy") if an application
connection tries to smuggle a row into a foreign workspace. This is
stronger than read-only filtering. The integration suite pins it
(`tests/integration/test_rls_isolation.py::test_insert_with_mismatched_scope_is_rejected`).

### Force-RLS implications

`FORCE ROW LEVEL SECURITY` subjects the table OWNER to the policy
unless the role has `BYPASSRLS`. The application MUST run as a
non-superuser role without `BYPASSRLS` (the `flycanon_app` role
provisioned in
[operations-runbook.md § RLS role provisioning](operations-runbook.md#5-rls-role-provisioning))
so the policies cannot be bypassed by accident. Migrations + workers
run as `flycanon_admin` with `BYPASSRLS` -- they legitimately read
across workspaces.

---

## 5. Idempotency replay protection

Agent-tier POSTs (`/api/v1/agent/*`) mandate the `Idempotency-Key`
header. The
[`IdempotencyStore`](../src/flycanon/web/conventions/idempotency.py)
namespaces every entry by the tuple `(tenant_id, route, key)`:

- **Cross-tenant.** Two tenants sending the same key against the
  same route do NOT collide; each has its own entry.
- **Cross-route.** Two distinct routes accepting the same key do
  NOT collide; the `scope` segment of the tuple keeps them
  separate.

A replay with the same key returns the cached response; a replay
with the same key but a different payload hash returns
`409 idempotency_key_conflict`. The user-tier surface accepts the
header optionally; the agent surface mandates it (documented in
[api-reference.md § Agent endpoints](api-reference.md#agent-endpoints)).

The key grammar is `^[A-Za-z0-9_\-]{1,128}$`. Values outside that
grammar are rejected at the conventions layer.

---

## 6. Rate limiting

Per-token sliding-window counter, keyed by `token_id`, with a 60 s
window. Configured per-token via `rate_limit_rpm` at mint time;
`None` (or `0`) disables the check.

A token over its budget gets `429 rate_limit_exceeded`. The limiter
implementation
([`agent_token_service.py`](../src/flycanon/core/services/auth/agent_token_service.py))
uses a per-token `_TokenBucket` (a `collections.deque` of monotonic
timestamps) guarded by a `threading.Lock`.

**Known limitation:** the limiter is **process-local**. A
multi-replica deploy multiplies the effective per-token rate by the
replica count -- a `rate_limit_rpm=60` token can issue ~60*N requests
per minute across N replicas. Until a Redis-backed counter shared
across replicas lands (tracked as a follow-up; see CHANGELOG), size
the limit conservatively or rely on an upstream gateway-level
rate-limiter.

---

## 7. Audit trail

Every mutation lands a row in `canon_audit_events` (also published
on the `flycanon.audit` EDA topic for near-realtime compliance
projections -- see [eda-events.md § flycanon.audit](eda-events.md#flycanonaudit)).

Each row carries:

| Field | Description |
|---|---|
| `id` | uuid of the audit row |
| `event_type` | normalised event name (`source.ingested`, `knowledge.published`, `candidate.accepted`, ...) |
| `subject_kind` | `source` / `knowledge_item` / `candidate` / `taxonomy` / `workspace` / `agent_token` |
| `subject_id` | id of the touched entity |
| `actor` | caller identity. For agent calls: `agent:<prefix>`. For user calls: JWT subject. For anonymous (dev): `anonymous`. |
| `tenant_id`, `workspace_id` | the verified scope |
| `correlation_id` | W3C correlation id from the originating request |
| `occurred_at` | server timestamp (ISO-8601, UTC) |
| `payload` | event-specific dict |

What's audited:

- Every agent verify (success and failure) lands a row.
- Idempotency replays log a "hit" -- the original write was audited
  on the first call; replays reuse the recorded response without a
  second write.
- Token revocations (`DELETE /api/v1/agent-tokens/{id}`) land an
  audit row before the next call's verify fails.
- Workspace mutations (`POST /api/v1/workspaces`,
  `PATCH /api/v1/workspaces/{id}`,
  `POST /api/v1/workspaces/{id}:close`) land an audit row + emit on
  `canon.workspaces.v1`.
- Knowledge mutations (create, update, supersede, retire), candidate
  mutations (propose, accept, reject), source mutations (ingest,
  replace) all land an audit row + emit on
  `flycanon.knowledge` / `flycanon.ingest`.

The `actor` value is the public identifier of the caller -- the
`agent:<prefix>` prefix is non-secret and safe to log / surface in
UIs. Cost attribution (`canon_cost_events`) uses the same `actor`
column so billing endpoints can attribute spend back to the minted
token.

---

## 8. Known limitations + accepted risks

| Limitation | Mitigation |
|---|---|
| Rate limits are per-process (see [§ 6](#6-rate-limiting)). Multi-replica deploys multiply the effective per-token rate. | Size `rate_limit_rpm` conservatively; deploy a gateway-level rate limiter in front of multi-replica clusters; Redis-backed sharing is a planned follow-up. |
| `BYPASSRLS` role compromise = total tenant data exposure across all workspaces. | Treat the admin DB credential as production-secret material; rotate on operator turnover; restrict the admin role's network reach. Standard Postgres reality, not a flycanon invariant. |
| SSE streams (`POST /api/v1/query/stream`, `POST /api/v1/agent/query/stream`, `GET /api/v1/ingest-jobs/{id}/stream`) cannot replay through idempotency -- the stream is stateful + per-connection. | Intentional. Clients re-subscribe with `?after_id=` (for ingest-job event streams) or repeat the query with the same `Idempotency-Key` if the answer is replayable as a single record. |
| Agent tokens are valid forever until `expires_at` or `DELETE`. | Recommend `expires_at` on every mint; the [consumers.md § Expiry recommendations](consumers.md#expiry-recommendations) suggests 90 days for production service-to-service. The `last_used_at` listing column flags dormant tokens for cleanup. A null `expires_at` is permitted by the schema (no enforcement) but strongly discouraged outside dev. |
| `canon_chunk_vectors` is runtime-created (see [§ 4](#4-rls-policies) and [operations-runbook.md § 10](operations-runbook.md#10-canon_chunk_vectors-deploy-ordering)). On a first deploy the table exists briefly without RLS. | The PgvectorStore bootstrap installs the policy in-band with table creation. Confirm via `SELECT polname FROM pg_policies WHERE tablename = 'canon_chunk_vectors'` after first boot. |
| The default deployment ships with no authentication (`FLYCANON_API_KEYS=`). | Production deployments MUST enable at least one auth mode -- static API keys via `FLYCANON_API_KEYS` or OAuth2 resource server via `pyfly.security.oauth2.resource-server.enabled=true`. See [deployment.md § Authentication](deployment.md#authentication). |
| EDA publish failures are logged but never abort the originating mutation (best-effort publish). Consumers may miss events during a broker outage. | The durable record is `canon_audit_events`; consumers can rebuild their projection from the table. The Postgres outbox (`pyfly_eda_outbox`) preserves unpublished events until the worker drains. |
| Idempotency store is in-memory in the default build; replays do not survive a process restart. | Production deployments are expected to swap for a Postgres-backed store (the `IdempotencyStore` protocol is the integration seam). |
| PII guardrail defaults to `warn` (index as-is + record findings). | Set `FLYCANON_PII_POLICY=redact` to rewrite sensitive spans before chunking + indexing, or `reject` to fail intake with `422 pii_violation`. See [pii.md](pii.md). |

---

## 9. Reporting vulnerabilities

Disclose security issues privately to **security@firefly.dev**.

- Provide a reproduction (curl one-liner if possible) and the
  flycanon version reported by `GET /api/v1/version`.
- Do not file public issues for vulnerabilities. The CHANGELOG
  records every fix once the disclosure window closes.
- Patch releases for security issues land as `YY.MM.x` increments
  and are documented in [CHANGELOG.md](../CHANGELOG.md).

---

## 10. Cross-references

- [architecture.md § Row-level security](architecture.md#row-level-security)
  -- the data-model perspective on the RLS contract.
- [api-reference.md § Agent surface](api-reference.md#agent-surface)
  -- the wire-level view of every agent route + scope.
- [consumers.md](consumers.md) -- the consumer-side contract for
  downstream services (scope grants, allowlist patterns, rotation
  procedure).
- [operations-runbook.md](operations-runbook.md) -- day-2 token
  rotation, RLS role provisioning, observability hooks, DR.
- [eda-events.md](eda-events.md) -- the four EDA topics flycanon
  publishes (`canon.workspaces.v1`, `flycanon.knowledge`,
  `flycanon.ingest`, `flycanon.audit`).
- [deployment.md § Authentication](deployment.md#authentication) --
  the static API key + OAuth2 resource-server gates.
- [pii.md](pii.md) -- the PII guardrail policy options.
- [flyradar/docs/security-model.md](../../flyradar/docs/security-model.md)
  -- the matching threat model on the flyradar side.
