# Changelog

All notable changes to **flycanon** are documented here.

## [Unreleased] -- Multitenancy backbone

### Added -- Plan 2 (Phase 2) workspace + multitenancy schema

- **`canon_workspaces` table** -- canonical store for workspace
  identity, status, scope, sme_roster, retention_days, jurisdiction.
  Migration `0008_workspaces`.
- **`(tenant_id, workspace_id)` columns** on every existing
  `canon_*` table (14 in total) -- NOT NULL with server default
  `'default'`. Existing rows backfilled to `'default'`/`'default'`.
  Composite `(tenant_id, workspace_id)` indexes added for
  workspace-scoped queries.
- **`Workspace` SQLAlchemy entity** + **`WorkspaceStatus` enum**
  + 4 DTOs (`WorkspaceCreate`, `WorkspaceUpdate`, `WorkspaceSpec`,
  `WorkspaceSummary`).
- **`WorkspaceRepository`** with CRUD operations (insert, get,
  list_for_tenant, update, close).
- **`flycanon.web.conventions` module** ported from flyradar
  (Plan 1) -- 90 tests covering RFC 7807 envelope, FastAPI
  dependency, exception hierarchy, idempotency primitives,
  tenant-safe HTTP client. Not yet wired into controllers
  (Plan 4).

### Added -- Plan 3 (Phase 3) embeddings hardening

- **Migration 0009** -- `canon_chunks` re-embed drift index swap:
  `(source_id, embedding_model)` -> `(tenant_id, workspace_id,
  embedding_model)`. Plus new composite
  `ix_canon_chunks_scope_source` for source fetches within a
  workspace.
- **Migration 0010** -- `canon_chunk_vectors` gains `tenant_id` +
  `workspace_id` columns with `'default'` server default, plus
  composite index `canon_chunk_vectors_scope`. Postgres-only;
  SQLite is a no-op.
- **`PgVectorVectorStore`** -- DDL creates scope columns from
  day one; `search()` filters scope + bumps `hnsw.ef_search` to
  200 per-query via `SET LOCAL`; SQL-side widening via
  `LIMIT k * widening_factor` (default 5).
- **`PostgresCorpus.bm25_search`** -- filters `(tenant_id,
  workspace_id)` before `tsv @@ plainto_tsquery`. Global GIN on
  `tsv` is intersected with the composite btree cheaply.
- **`RetrievalService.search()`** -- fails closed
  (`MissingTenantContext`) when scope is missing; threads
  `tenant_id` + `workspace_id` through scope-bound proxies to
  the HybridRetriever.
- **`IndexService.replace_for_source()`** -- accepts optional
  scope with `'default'` fallback (Plan 4 tightens).
- **Tier-B partition admin** in
  `flycanon.core.services.retrieval.partition_admin`. Dormant
  by default. `promote_tenant_to_partition()` /
  `demote_tenant_from_partition()` for hot-tenant operators.

### BREAKING -- Plan 4 (Phase 4) conventions adoption + controller wiring

- **Headers required everywhere.** Every `/api/v1/*` call (except
  `/api/v1/version`) now requires `X-Tenant-Id` + `X-Workspace-Id`.
  Missing -> `400 missing_tenant_context`.
- **Error envelope flipped.** `title` is now human-readable; new
  `code` field carries the machine identifier; `type` URI base
  `https://firefly.dev/problems/...` (was `https://flycanon.dev/...`).
  Media type `application/problem+json`.
- **`/api/v1/jobs/*` -> `/api/v1/ingest-jobs/*`** -- 3 sub-routes
  renamed.
- **`actor` partitioning proxy retired.** `actor` stays as audit
  metadata on every row; queries/groupings now use
  `(tenant_id, workspace_id)`. Billing endpoints no longer accept
  `actor` Query param.
- **New `/api/v1/workspaces` CRUD** -- create/list/get/patch/close
  on `canon_workspaces` (table from Plan 2).
- **`canon_sources.content_sha256` unique constraint widened** to
  `(tenant_id, workspace_id, content_sha256)`. Same content can
  coexist in multiple workspaces.
- **Entity-level `'default'` defaults dropped** -- services now
  pass real `tenant_id` + `workspace_id` from `TenantContext`.
  Migration `0011` drops the column-level `server_default` too.
- **RetrievalService callers now operational** -- Plan 3 had
  `/api/v1/search`, `/api/v1/query`, `/api/v1/query/stream`
  returning 400 because their callers didn't pass scope. Plan 4
  wires the threading.

### Added -- Plan 5 (Phase 5) agent surface

- **Agent tokens**: new `canon_agent_tokens` table (migration
  `0012_agent_tokens`). Tokens are tenant-scoped, hashed at rest,
  carry optional `workspace_allowlist`, `scopes` list,
  `rate_limit_rpm`, and `expires_at`.
- **User-tier CRUD**: `POST /api/v1/agent-tokens` (mint -- returns
  the full secret ONCE), `GET /api/v1/agent-tokens` (list -- secrets
  redacted), `DELETE /api/v1/agent-tokens/{id}` (revoke).
- **Agent surface (X-Agent-Token-protected, 8 endpoints):**
  - `POST /api/v1/agent/sources` (scope `agent.sources:ingest`)
  - `GET /api/v1/agent/sources/{id}` (scope `agent.sources:read`)
  - `POST /api/v1/agent/query` (scope `agent.query:run`)
  - `POST /api/v1/agent/query/stream` (SSE; scope `agent.query:run`)
  - `POST /api/v1/agent/search` (scope `agent.query:run`)
  - `GET /api/v1/agent/knowledge/{id}` (scope `agent.knowledge:read`)
  - `GET /api/v1/agent/knowledge/{id}/provenance` (scope `agent.knowledge:read`)
  - `POST /api/v1/agent/candidates:propose` (scope `agent.candidates:propose`)
- **Mandatory `Idempotency-Key`** on agent-tier POSTs.
- **New error codes**: `missing_agent_token` (401),
  `invalid_agent_token` (403), `agent_token_expired` (403),
  `agent_workspace_not_in_allowlist` (403), `agent_scope_denied` (403),
  `agent_cannot_mint` (403).

### Security -- Plan 6 (Phase 6) tenant lockdown

- **Workspace-scope enforcement on every read-by-id route.** Previously,
  a caller who guessed a resource UUID from another workspace in the
  same tenant could read knowledge / sources / candidates / etc. Now
  all such lookups return `404 resource_not_found`. Affects both the
  user tier and the agent tier (the Plan 5 `/api/v1/agent/*`
  surface). Repositories, handlers, controllers, and CQRS queries
  now thread `workspace_id` alongside `tenant_id`.
- **Postgres RLS on every `canon_*` table** (16 tables) -- migration
  `0013_rls_policies`. Defence-in-depth: even if a repository forgets
  a WHERE clause, RLS returns zero rows for the wrong scope. The
  migration is Postgres-only; SQLite is a no-op so unit tests stay
  green. The USING-only policies auto-derive `WITH CHECK`, so
  cross-scope **INSERTs** are also blocked (psycopg raises
  `InsufficientPrivilege` -- stronger than read-only filtering).
  Special-case tables: `canon_workspaces` matches `tenant_id` + `id`,
  `canon_agent_tokens` matches `tenant_id` only (tokens span
  workspaces), and `canon_chunk_vectors` is guarded by a runtime
  `DO`-block `IF EXISTS` check because it's created by `PgvectorStore`
  at boot rather than by Alembic.
- **Session GUC plumbing.** `flycanon.web.conventions.db` exposes
  `set_tenant_guc(session, ctx)` for explicit use, plus
  `install_tenant_guc_hook()` which registers a SQLAlchemy
  `after_begin` listener on the synchronous Session underneath every
  AsyncSession. The hook fires on every `build_engine()` in
  `models/repositories/_engine.py` (idempotent). Transactions opened
  outside a request context (workers, retention sweeps, migration
  runner) skip the GUCs cleanly so cross-workspace access keeps
  working under `BYPASSRLS`.
- **Deployment requirement.** Ops must provision (a) a `flycanon_admin`
  Postgres role with `BYPASSRLS` for migrations and cross-workspace
  workers (consolidation re-embed sweep, retention reaper, EDA
  ingest worker), and (b) a separate `flycanon_app` role **without
  superuser** for the request-path engine. `FORCE ROW LEVEL SECURITY`
  applies to the table OWNER but NOT to Postgres superusers, so a
  superuser-connected service would silently bypass the policy.
  Documented in `docs/architecture.md -> Row-level security`.
- **RLS integration coverage.** New
  `tests/integration/test_rls_isolation.py` boots a real Postgres +
  pgvector via testcontainers, runs migrations as a `BYPASSRLS`
  admin role, then exercises 11 scenarios as a non-bypass `app_user`:
  cross-workspace + cross-tenant reads, unset-GUC zero-rows, the
  `canon_workspaces` / `canon_agent_tokens` special cases, the
  runtime `canon_chunk_vectors` table, write-path INSERT rejection,
  multi-table GUC coherence, `SET LOCAL` transaction scoping, and
  the FORCE-vs-owner contract. Cleanly skipped when Docker is
  unavailable.

### Added -- Plan 6 workspace lifecycle events

- **`canon.workspaces.v1` topic** carries `WorkspaceCreated`,
  `WorkspaceUpdated`, `WorkspaceDeleted` events emitted by
  `workspaces_controller`. Lifecycle mapping: `POST /workspaces` ->
  `WorkspaceCreated`, `PATCH /workspaces/{id}` -> `WorkspaceUpdated`,
  `POST /workspaces/{id}:close` -> `WorkspaceDeleted` (semantic:
  workspace closed, not row deleted -- the row is preserved with
  `status=closed`).
- **`WorkspaceEventPublisher`** routes through the existing pyfly
  `EventPublisher` bean (same transport as the
  `flycanon.audit` / `flycanon.knowledge` / `flycanon.ingest` topics).
  Default backend is the Postgres outbox; flip
  `FLYCANON_EDA_ADAPTER` to `memory` / `redis` / `kafka` to swap.
- **Best-effort consistency.** A failed publish is logged and
  swallowed -- it does NOT roll back the mutation (durable truth is
  the workspace row in Postgres + the parallel
  `canon_audit_events` row). Concurrent mutations may emit events
  out of order; consumers reconcile via the workspace row's
  `updated_at` (last-write-wins). Schema docs:
  [docs/payload-reference.md -> Workspace lifecycle events](docs/payload-reference.md#workspace-lifecycle-events).

### Removed

- **Legacy `flycanon.web.problem_handlers`** -- superseded by
  `flycanon.web.conventions.register_exception_handlers`.
- **Legacy `flycanon.interfaces.dtos.error.ProblemDetails`** (plural)
  -- superseded by `flycanon.web.conventions.ProblemDetail` (singular).
- **Billing actor Query param + actor filter on cost queries** --
  partitioning moves to tenant/workspace.

### Deferred / not yet shipped

- **Embedding cache key** -- spec section 4.3 forward-looking;
  no cache exists yet.
- **Live testcontainer tests for `partition_admin`** -- pure
  Python helpers are unit-tested; promote/demote require a real
  Postgres + partitioned `canon_chunk_vectors`.
- **`canon_chunk_vectors` RLS auto-install in `PgvectorStore`
  bootstrap.** Migration `0013` guards the policy with `IF EXISTS`
  because the table is created at boot, not by Alembic. On a
  first-deploy where `0013` runs before `PgvectorStore` creates the
  table, the table arrives without RLS. Operational workaround:
  either pre-create the table before running migrations, or wait for
  the follow-up patch that installs the policy alongside the table
  DDL inside `PgvectorStore`.
- **flyradar workspace cache client** -- consumes
  `canon.workspaces.v1` events; lives in flyradar.
- **`POST /api/v1/agent/canon/handoff`** -- flyradar-side endpoint
  that calls flycanon's `POST /api/v1/agent/sources`.
- **Workspace event transport tuning** -- the publisher rides on
  the existing pyfly Postgres outbox by default; production Kafka /
  RabbitMQ tuning is a follow-up cross-cutting plan.
- **SDK propagation** -- Python + Java SDKs are now 9 plans behind.
- **Load + pen-test** -- the unification spec's Phase 4 validation
  pass ("5k/50k/500k chunks across 10/100/1000 tenants" +
  pen-test RLS gates).
- **Constant-time hash compare on agent token verify** +
  **`last_used_at` write deduplication** -- both flagged in Plan 5
  review; must be back-ported to flyradar in lock-step.
- **Rate limiting**: `rate_limit_rpm` on agent tokens is stored
  but not enforced.
- **Knowledge create/update via agent**: deliberately excluded per
  spec section 5.2 -- agent callers must use
  `POST /api/v1/agent/candidates:propose` and let a user-tier
  reviewer accept.
- **Taxonomy / billing / stats agent endpoints**: out of scope per
  spec section 5.2.
