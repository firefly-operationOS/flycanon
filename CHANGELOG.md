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
- **Workspace cache client + EDA publisher** for flyradar --
  Plan 6.
- **RLS** -- Plan 6.
- **Rate limiting**: `rate_limit_rpm` on agent tokens is stored
  but not enforced.
- **Knowledge create/update via agent**: deliberately excluded per
  spec section 5.2 -- agent callers must use
  `POST /api/v1/agent/candidates:propose` and let a user-tier
  reviewer accept.
- **Taxonomy / billing / stats agent endpoints**: out of scope per
  spec section 5.2.
