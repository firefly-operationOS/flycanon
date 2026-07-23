# Changelog

All notable changes to **flycanon** are documented here.

## [Unreleased]

## [26.7.0] - 2026-07-23

### Added

- **Agent-tier source replace.** `PUT /api/v1/agent/sources/{source_id}` (scope
  `agent.sources:ingest`, mandatory `Idempotency-Key`) re-ingests an existing
  source in place through the same pipeline as the user-tier PUT: chunks and
  dense vectors are replaced under the same `source_id`. `content_base64` is
  required; replays dedup under a route-specific scope.
- **Agent-tier source delete.** `DELETE /api/v1/agent/sources/{source_id}`
  (scope `agent.sources:ingest`, mandatory `Idempotency-Key`, `204`) removes a
  source within the tenant/workspace scope: BM25 rows and dense vectors are
  purged, chunk rows and the source row are deleted, an audit entry is recorded
  and a `SourceRemoved` EDA event is published. Object-store originals are not
  touched. A retried DELETE with the same key replays the original `204`.

## [26.6.18] - 2026-06-18

### Added

- **RLM (Recursive Language Model) answer engine.** A code-driven CodeAct REPL
  that reasons over whole documents instead of retrieving chunks: it routes to
  the right source, reads it, and computes the answer in a sandboxed Python
  loop. New `FLYCANON_RLM_*` settings tune the root/sub/answer models
  (default `anthropic:claude-sonnet-4-6`), iteration budget, and depth.
- **Object-store originals.** A hexagonal `ObjectStore` port (LocalFs / S3) plus
  `FLYCANON_OBJECT_STORE_*` settings; ingest persists the original document and
  records `canon_sources.object_store_key` when `FLYCANON_STORE_ORIGINALS` is
  on (default), giving RLM a whole-document corpus.
- **Scalable corpus access.** The corpus loads lazily (only the filings the
  model actually reads are fetched) behind a tiered page cache — a thread-safe
  in-process LRU and an optional shared Redis layer (`FLYCANON_CORPUS_CACHE_*`),
  content-hash keyed so a re-ingest auto-invalidates.
- **Prompt caching** for the RLM loop (`FLYCANON_RLM_PROMPT_CACHE`, default on):
  the repeated system prompt is marked `cache_control: ephemeral`.

### Changed

- **RLM is now the default answer mode** (`FLYCANON_ANSWER_MODE=rlm`). It backs
  `/api/v1/query`, the SSE `/api/v1/query/stream` (a `status` frame then the
  `final` frame), and the agent-tier equivalents, with full filter parity.

### Deprecated

- **Hybrid-RAG answer mode** (`FLYCANON_ANSWER_MODE=rag`) is deprecated and
  slated for removal in a future release. When selected it logs a deprecation
  warning and returns an `X-Flycanon-Deprecation` response header. The raw
  `/api/v1/search` retrieval surface is unaffected.

## [26.5.9] - 2026-05-31

### Changed

- **Open-sourced under the Apache License 2.0.** Replaced the proprietary
  notice with the full Apache 2.0 `LICENSE` (root + both SDKs) and prepended
  the Apache 2.0 header to every source file. The copyright holder is now
  Firefly Software Foundation, and the repository is public.
- Set the OpenAPI `info.license`, the image `licenses` label, the README
  badge, and `pyproject` metadata to Apache-2.0.
- Realigned `__version__` with the packaged release version.

## [Unreleased] -- Multitenancy backbone

### Added -- multi-backend vector stores (26.5.8)

- **Pluggable dense backend.** `FLYCANON_VECTOR_STORE` now selects the dense
  projection backend: `pgvector` (default), `qdrant`, or `chroma`. The lexical
  BM25 half and RRF fusion always stay on Postgres. Qdrant/Chroma use the
  adapters that ship in `fireflyframework-agentic` (install `--extra qdrant` /
  `--extra chroma`); new `FLYCANON_QDRANT_*` / `FLYCANON_CHROMA_*` settings
  configure them.
- **Explicit, fail-loud scope contract.** Every dense backend is wrapped in the
  framework's `TenantScopedVectorStore`, which folds `(tenant_id, workspace_id)`
  into a canonical `t/<tenant>/w/<workspace>` namespace. The previous
  `inspect.signature` scope-probe (which could silently skip isolation on a
  scope-blind store) is gone; missing scope now fails loud.
- **pgvector moved upstream.** flycanon's bespoke pgvector adapter was replaced
  by the framework's generic `PgVectorVectorStore`; flycanon keeps a thin
  `RlsPgVectorVectorStore` subclass that adds namespace-keyed Postgres RLS via
  the adapter's `_prepare_session` hook. Requires `fireflyframework-agentic`
  >= 26.5.32. Migration `0014` retires the legacy column-shaped
  `canon_chunk_vectors` table (the adapter recreates the namespace-shaped table
  on boot; **re-index after upgrading** to repopulate the derived projection).
- **Vector purge on source delete.** `IndexService.remove_for_source` now purges
  the dense vectors for a deleted source's chunks (external stores have no FK
  cascade).

### Changed -- retrieval and intake internals (26.5.7)

- **Native per-format document intake.** Document intake runs on native
  per-format SourceLoaders: `XlsxLoader` (openpyxl), `PptxLoader`
  (python-pptx), `CsvLoader` (CSV/TSV, stdlib), `JsonLoader` (stdlib),
  `XmlLoader` (lxml), `RtfLoader` (striprtf) and `OdfLoader` (ODT/ODS/ODP,
  odfpy). The registry fallback is a plain-text `TextLoader`, so an
  unrecognised payload degrades to UTF-8 text. `striprtf` + `odfpy` are
  declared as direct deps.
- **Binary normalisation.** Binary normalisation runs through
  `fireflyframework_agentic.content.binary` (the unified normaliser shared
  with flydocs), wired in `CanonCoreConfiguration` from a `BinaryConfig`
  mapped off `CanonSettings` (`wrap_text_as_pdf=False` -- text passes
  through to the SourceLoaders; `email_render_header=True`). The
  `OfficeConverter` stays pluggable.
- **Hybrid retrieval primitives.** `StoredChunk`, `ChunkHit`,
  `reciprocal_rank_fusion` and `HybridRetriever` live in
  `core/services/retrieval/fusion.py`. `HybridRetriever` honours
  `FLYCANON_RETRIEVAL_RRF_K`.
- **Hybrid retrieval, pluggable dense half.** `corpus_factory` keeps BM25 on
  Postgres and routes the dense half to the configured backend (see *Added --
  multi-backend vector stores* above). _Previously pgvector-only._
- **Dependencies.** `fireflyframework-agentic` is pulled with the
  `[openai-embeddings,binary,vectorstores-pgvector]` extras; the `[binary]`
  extra provides pillow-heif, cairosvg, py7zr and extract-msg. Qdrant/Chroma
  client libraries come from the framework's `vectorstores-*` extras via
  flycanon's optional `qdrant` / `chroma` extras.

### Added -- Redis-backed adapters for rate-limit + idempotency

- **`RedisRateLimiter`** -- sliding-window per-token rate limiter
  using a Redis ZSET + Lua script (atomic across replicas). Opt-in
  via `FLYCANON_REDIS_URL` (`auto` picks Redis when the URL is set)
  or forced via `FLYCANON_RATE_LIMIT_BACKEND=redis`. Lives next to
  the in-memory `_RateLimiter` behind the new `RateLimiter`
  Protocol so `AgentTokenService` accepts either via constructor
  injection.
- **`RedisIdempotencyStore`** -- Redis-string-backed replay store
  with native TTL. Same env-var toggle
  (`FLYCANON_IDEMPOTENCY_BACKEND=redis` or `auto`+`redis_url`).
  Replaces the in-process LRU cap (`max_entries=100_000` on the
  in-memory variant) with native Redis TTL eviction for production
  scale.
- **`IdempotencyStore.lookup` / `record_response` are now async**
  to honour the Redis adapter's coroutine signature; the in-memory
  variant keeps the same dict-mutation body under `async def`.
  Helpers `check_idempotency_replay` / `store_idempotent_response`
  and every agent controller that calls them now `await` the
  store. The legacy sync `get` / `put` surface is unchanged so the
  workspace-scoped index tests keep working.
- **`RateLimiter` Protocol** -- new public surface in
  `core/services/auth/agent_token_service.py`. Both
  `_RateLimiter.consume`/`drop` and `RedisRateLimiter.consume`/`drop`
  are async; `AgentTokenService.verify` / `.revoke` await them.
- In-memory adapters remain the default when `redis_url` is absent
  / `FLYCANON_RATE_LIMIT_BACKEND` and `FLYCANON_IDEMPOTENCY_BACKEND`
  are `in_memory`. Behaviour wire-stable across the swap.

### Added -- agent-token rate limiting

- **`rate_limit_rpm` enforcement** -- `AgentTokenService.verify`
  now consults a per-token sliding-window counter (60s, keyed by
  `token_id`). Tokens that exceed their `rate_limit_rpm` budget get
  `429 rate_limit_exceeded` (new `RateLimitExceeded` exception);
  `rate_limit_rpm = None` (or `<= 0`) skips the check entirely so
  legacy tokens are unaffected. `revoke` drops the in-memory bucket
  so a re-mint that reuses the `token_id` starts fresh.
- **In-memory only / process-local** -- the bucket dict lives in
  the `AgentTokenService` instance behind a `threading.Lock`. This
  is intentional MVP posture, matching the rest of the service.
  A Redis-backed counter shared across replicas is available as
  a pluggable adapter (the `_RateLimiter` Protocol is the
  integration seam); exception class and status code are
  wire-stable across the swap.
- **New error code**: `rate_limit_exceeded` (429).

### Added -- workspace + multitenancy schema

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
- **`flycanon.web.conventions` module** -- 90 tests covering RFC 7807
  envelope, FastAPI dependency, exception hierarchy, idempotency
  primitives, tenant-safe HTTP client. Wired into controllers (see
  BREAKING section below).

### Added -- embeddings hardening

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
- **`IndexService.replace_for_source()`** -- requires `tenant_id`
  + `workspace_id` kwargs with no default.
- **Tier-B partition admin** in
  `flycanon.core.services.retrieval.partition_admin`. Dormant
  by default. `promote_tenant_to_partition()` /
  `demote_tenant_from_partition()` for hot-tenant operators.

### BREAKING -- conventions adoption + controller wiring

- **Headers required everywhere.** Every `/api/v1/*` call (except
  `/api/v1/version`) requires `X-Tenant-Id` + `X-Workspace-Id`.
  Missing -> `400 missing_tenant_context`.
- **Error envelope.** `title` is human-readable; the `code` field
  carries the machine identifier; `type` URI base is
  `https://firefly.dev/problems/...`. Media type
  `application/problem+json`.
- **`/api/v1/jobs/*` -> `/api/v1/ingest-jobs/*`** -- 3 sub-routes
  renamed.
- **`actor` is audit metadata.** `actor` stays as audit metadata on
  every row; queries/groupings use `(tenant_id, workspace_id)`.
  Billing endpoints do not accept an `actor` Query param.
- **New `/api/v1/workspaces` CRUD** -- create/list/get/patch/close
  on `canon_workspaces`.
- **`canon_sources.content_sha256` unique constraint widened** to
  `(tenant_id, workspace_id, content_sha256)`. Same content can
  coexist in multiple workspaces.
- **Entity-level `'default'` defaults dropped** -- services pass
  real `tenant_id` + `workspace_id` from `TenantContext`. Migration
  `0011` drops the column-level `server_default` too.
- **RetrievalService scope threading.** `/api/v1/search`,
  `/api/v1/query`, and `/api/v1/query/stream` thread scope from the
  request headers into the retrieval layer.

### Added -- agent surface

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

### Security -- tenant lockdown

- **Workspace-scope enforcement on every read-by-id route.** A caller
  who guesses a resource UUID from another workspace in the same
  tenant gets `404 resource_not_found` instead of the foreign row.
  Affects both the user tier and the agent tier (the `/api/v1/agent/*`
  surface). Repositories, handlers, controllers, and CQRS queries
  thread `workspace_id` alongside `tenant_id`.
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

### Added -- workspace lifecycle events

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

### Pluggable adapters / external infra

- **Rate-limit + idempotency adapters.** The in-process
  `_TokenBucket` rate limiter and `InMemoryIdempotencyStore` are the
  shipped defaults; the Redis-backed `RedisRateLimiter` and
  `RedisIdempotencyStore` ship in this release as opt-in adapters
  (`FLYCANON_RATE_LIMIT_BACKEND=redis` /
  `FLYCANON_IDEMPOTENCY_BACKEND=redis`, `auto` picks Redis when
  `FLYCANON_REDIS_URL` is set). Exception classes and status codes
  are wire-stable across the swap. See the "Redis-backed adapters"
  block at the top of this release for the implementation details.
- **Workspace event transport.** The publisher rides on pyfly's
  Postgres outbox by default; flip `FLYCANON_EDA_ADAPTER` to
  `memory` / `redis` / `kafka` to swap. Operator chooses the
  transport adapter at deploy time; no code change required.
- **OpenTelemetry tracing export.** Spans are already emitted to
  pyfly's observability stack and W3C `traceparent` /
  `tracestate` headers propagate across the cross-service handoff.
  Export to an external collector (Jaeger / Tempo) is an ops
  choice driven by the `pyfly.tracing` configuration.
- **Read-replica routing.** Single-primary Postgres is the shipped
  topology. The request path stays on the primary; read-replica
  routing is on the platform roadmap, not blocked in code.

### Deliberately excluded by design (spec § 5.2)

These are NOT deferred -- they are rejected agent-tier endpoints.
The user-tier surface remains the only path:

- **Knowledge create/update via agent.** Agent callers must use
  `POST /api/v1/agent/candidates:propose` and let a user-tier
  reviewer accept.
- **Taxonomy / billing / stats agent endpoints.** Out of scope per
  spec section 5.2.

### Roadmap items

- **Embedding cache key.** A forward-looking optimisation; no cache
  exists yet. Tracked on the roadmap.
- **Load + pen-test.** External validation pass ("5k/50k/500k chunks
  across 10/100/1000 tenants" + pen-test RLS gates). Awaiting an
  external validation window.
