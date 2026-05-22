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

### Deferred / not yet shipped

- **CRUD controller `/api/v1/workspaces`** -- needs
  `require_tenant_context()` from the conventions module. Plan 4.
- **Service-layer scope threading** -- services still rely on the
  `'default'` server default for `tenant_id` + `workspace_id`.
  Plan 4 wires real values.
- **Widened unique constraints** (e.g.
  `canon_sources.content_sha256` -> composite) -- Plan 4 (coupled
  with service-layer adoption).
- **`actor` field retirement** -- Plan 4.
- **Wiring scope from request headers into RetrievalService** --
  Plan 4 (conventions adoption). Today `/api/v1/search` /
  `/api/v1/query` / `/api/v1/query/stream` return 400 because
  their callers don't yet pass `tenant_id` / `workspace_id`.
  This is the intentional fail-closed surface of Plan 3.
- **Embedding cache key** -- spec section 4.3 forward-looking;
  no cache exists yet.
- **Live testcontainer tests for `partition_admin`** -- pure
  Python helpers are unit-tested; promote/demote require a real
  Postgres + partitioned `canon_chunk_vectors`.
- **Workspace cache client + EDA publisher** for flyradar --
  Plan 6.
- **Agent surface `/api/v1/agent/*`** + canon handoff -- Plan 5.
- **RLS** -- Plan 6.
