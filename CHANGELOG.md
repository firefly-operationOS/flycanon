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
- **Embeddings hardening** (pgvector + BM25 + scope columns +
  `ef_search` tuning) -- Plan 3 (the high-risk plan).
- **Agent surface `/api/v1/agent/*`** + canon handoff -- Plan 5.
- **RLS** -- Plan 6.
