# Multi-Backend Vector Stores for flycanon (Qdrant, Chroma, pgvector) — Design

- **Date:** 2026-05-31
- **Status:** Approved (brainstorming complete)
- **Author:** andres.contreras@soon.es (with Claude)
- **Repos touched:** `fireflyframework-agentic` (framework) + `flycanon` (consumer)

## 1. Problem & goal

flycanon's retrieval layer is Postgres-native end-to-end. The dense vector
projection is hard-wired to pgvector: `corpus_factory.build_corpus_context`
**rejects any `FLYCANON_VECTOR_STORE` value except `pgvector`**
(`corpus_factory.py:51-52`). We want flycanon to support additional vector
backends — **Qdrant** and **Chroma** for v1 — professionally, behind a clean
hexagonal port, while keeping the framework as the home of reusable
infrastructure.

The framework (`fireflyframework-agentic`) already owns the vector-store port
(`VectorStoreProtocol` + `BaseVectorStore`) and ships adapters for Chroma,
Pinecone, Qdrant, sqlite-vec, and in-memory — but **no pgvector adapter**, and
its port is **scope-less** (single `namespace`, no tenant/workspace).

## 2. Decisions (locked)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Dense projection goes external; lexical BM25 + RRF stay on Postgres.** New backends replace only the dense half. `PostgresCorpus` (BM25 over the GENERATED `tsv` + GIN) and `HybridRetriever` RRF fusion are unchanged. | The split architecture already supports it; external DBs can't reproduce Postgres FTS quality. Postgres remains a hard dependency. |
| D2 | **Move a generic `PgVectorVectorStore` into the framework** as a peer of Qdrant/Chroma. | Fills the framework's only real gap; makes flycanon a thin consumer. |
| D3 | **Add a reusable scope layer to the framework** (`ScopedVectorStore` port + `TenantScopedVectorStore` wrapper), additive and non-breaking. | The framework's scope-less port stays intact for other users; one wrapper makes **any** backend multi-tenant by folding `(tenant_id, workspace_id)` into `namespace`. |
| D4 | **Isolation model: one collection/table per backend, `namespace = "t/<tenant>/w/<workspace>"`** as the scope key (natively indexed/filtered by every backend). Metadata `tenant_id`/`workspace_id` are stamped as defense-in-depth/diagnostics. | Uniform across pgvector/Qdrant/Chroma; namespace is the canonical, indexed isolation key. |
| D5 | **Explicit, fail-loud scoped port.** `tenant_id`/`workspace_id` are required keyword-only args; a non-compliant store fails at construction/type level. **Delete the `inspect.signature` probe** in flycanon. | Today scope is duck-typed; a scope-blind store **silently** loses isolation (`retrieval_service.py:484-494`). Fail-loud closes the footgun. |
| D6 | **RLS stays in flycanon**, via a thin `RlsPgVectorVectorStore` subclass. The generic upstream adapter exposes a no-op `_prepare_session(conn, *, namespace)` hook; flycanon's subclass uses it to `SET LOCAL` the scope GUC and installs a **namespace-based** RLS policy in `initialise()`. | pgvector co-locates with the system-of-record and can offer DB-enforced isolation that external DBs can't — it should use that strength. RLS needs app-level GUC coupling that doesn't generalize, so it lives in flycanon. The scoped *port* is still uniform across all backends. |
| D7 | **Dev via editable local path, then tag + re-pin.** Point flycanon's `fireflyframework-agentic` at the editable clone during development; cut a new framework tag (`v26.05.31`) when validated; re-pin flycanon. | Matches how `pyfly` is already consumed; enables end-to-end validation before release. |

**Out of scope (v1):** Pinecone wiring (framework ships it; trivially addable later because the adapter + scope wrapper are generic), Weaviate/Milvus (no framework adapter), per-tenant-collection isolation, native sparse/hybrid in external stores.

## 3. Key enabler

The vector store is a **derived projection** — `canon_chunks` (Postgres) is the
system-of-record. Swapping backends or reshaping the vector table is handled by
**re-indexing**, not data migration. This makes the pgvector table reshape (§6)
low-risk.

## 4. Framework changes (`fireflyframework-agentic`)

Convention: vectorstores modules use **no copyright header** (module docstring
first), `from __future__ import annotations`, lazy optional-dep import at module
top (`try/except ImportError`), `line-length = 120`, ruff `N/UP/B/SIM/TC` +
`PLC0415` (no inline imports outside tests/examples). asyncio auto mode.

### 4.1 `vectorstores/pgvector_store.py` — new generic adapter
- `PgVectorVectorStore(BaseVectorStore)`, asyncpg-based (matches the framework's
  existing `postgres` extra and `PostgreSQLStore` pattern). Optional dep guard:
  `try: import asyncpg ... except ImportError: asyncpg = None`; raise `ImportError`
  in `__init__` if missing (message names the `vectorstores-pgvector` extra).
- Constructor: `(url, *, dimension, table_name="vector_documents", hnsw_m=16,
  hnsw_ef_construction=64, hnsw_ef_search=200, pool_min_size=1, pool_max_size=10,
  embedder=None)`.
- **Namespace-centric schema** (no tenant/workspace columns — that's app-level):
  ```sql
  CREATE TABLE IF NOT EXISTS <table> (
      id         TEXT PRIMARY KEY,
      namespace  TEXT NOT NULL DEFAULT 'default',
      embedding  vector(<dim>) NOT NULL,
      text       TEXT NOT NULL,
      metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m, ef_construction);
  CREATE INDEX ... (namespace);
  ```
- Lazy `_ensure_pool()` creates an `asyncpg` pool (vector codec registered via
  `pgvector.asyncpg.register_vector` in the pool `init` callback) and runs an
  idempotent `_create_schema()` once.
- Implements `_upsert/_search/_delete` (the `BaseVectorStore` abstract methods).
  Each runs inside `async with pool.acquire() as conn, conn.transaction():` and
  calls `await self._prepare_session(conn, namespace=namespace)` first.
  - `_search`: `SET LOCAL hnsw.ef_search = <hnsw_ef_search>`, then
    `SELECT ... WHERE namespace = $1 ORDER BY embedding <=> $2 LIMIT $3`,
    score = `1 - (embedding <=> q)`. Honors `top_k` as given (caller controls
    over-fetch); applies `SearchFilter`s against `metadata` JSONB (`eq`/`in` etc.).
  - `_upsert`: `INSERT ... ON CONFLICT (id) DO UPDATE` (executemany).
  - `_delete`: `DELETE WHERE namespace = $1 AND id = ANY($2)`.
- **`async def _prepare_session(self, conn, *, namespace: str) -> None: return None`**
  — documented extension hook (default no-op) for per-transaction session setup
  (search_path, statement_timeout, RLS GUCs).
- `async def initialise(self)` → `await self._ensure_pool()`; `async def close(self)`
  → close pool. Wrap backend errors in `VectorStoreError` / `VectorStoreConnectionError`.
- Register in `vectorstores/__init__.py` (`PgVectorVectorStore`, sorted `__all__`).

### 4.2 `vectorstores/scoped.py` — new scope layer
- Helpers: `scope_namespace(tenant_id, workspace_id) -> "t/<t>/w/<w>"` and
  `parse_scope_namespace(ns) -> (tenant_id, workspace_id)` (raises `ValueError` on
  malformed input).
- `@runtime_checkable class ScopedVectorStore(Protocol)` with **required
  keyword-only** scope:
  ```python
  async def upsert(self, documents, *, tenant_id, workspace_id) -> None: ...
  async def search(self, query_embedding, top_k=5, *, tenant_id, workspace_id,
                   filters=None) -> list[SearchResult]: ...
  async def delete(self, ids, *, tenant_id, workspace_id) -> None: ...
  async def initialise(self) -> None: ...
  async def close(self) -> None: ...
  ```
- `class TenantScopedVectorStore` wraps any `VectorStoreProtocol`:
  - derives `namespace = scope_namespace(tenant_id, workspace_id)`;
  - on `upsert`, copies each `VectorDocument`, sets `.namespace` and stamps
    `metadata["tenant_id"]/["workspace_id"]` (no mutation of caller objects);
  - on `search`/`delete`, forwards `namespace=...` (+ caller filters) to the inner
    scope-less store;
  - `initialise()`/`close()` delegate to the inner store if present.
  - Missing scope → `TypeError` (keyword-only, no default) — fail-loud.
- Export `ScopedVectorStore`, `TenantScopedVectorStore`, `scope_namespace`,
  `parse_scope_namespace` from `vectorstores/__init__.py`.

### 4.3 `pyproject.toml`
- `version` `26.05.30` → `26.05.31`.
- New extra: `vectorstores-pgvector = ["asyncpg>=0.30.0", "pgvector>=0.3.0"]`;
  add to the `all` aggregate.

### 4.4 Framework tests (`tests/unit/vectorstores/`)
- `test_pgvector_store.py`: unit tests mocking `asyncpg` (mirroring
  `test_qdrant_store.py` mock style) for upsert/search/delete/import-error/ctor;
  an `integration`-marked test against a Testcontainers Postgres+pgvector
  (`testcontainers` is in the `dev` extra) covering real schema bootstrap + ANN.
- `test_scoped.py`: hermetic tests over `InMemoryVectorStore` proving
  cross-scope isolation (foreign-scope search returns nothing), namespace
  encoding/round-trip, metadata stamping, and `TypeError` on missing scope.
- Update `test_init.py` exports.

## 5. flycanon changes

### 5.1 Dependency (`pyproject.toml`)
- `[tool.uv.sources]`: switch `fireflyframework-agentic` to
  `{ path = "../../fireflyframework/fireflyframework-agentic", editable = true }`
  for development; re-pin to `{ git = ..., tag = "v26.05.31" }` after release.
- Agentic extras: add `vectorstores-qdrant`, `vectorstores-chroma`,
  `vectorstores-pgvector` to the `fireflyframework-agentic[...]` requirement;
  retire flycanon's local `chroma`/`qdrant`/`pinecone` raw-client extras in favor
  of the framework's `vectorstores-*` extras (single source of version truth).

### 5.2 `core/services/retrieval/pgvector_store.py` → `RlsPgVectorVectorStore`
- Replace the bespoke SQLAlchemy adapter with a thin subclass of the framework's
  `PgVectorVectorStore`:
  - override `_create_schema()` (or extend `initialise()`) to additionally install
    an idempotent, soft-failing **namespace-based** RLS policy:
    `USING (namespace = current_setting('app.scope_namespace', true))` +
    `WITH CHECK (...)`, `ENABLE`/`FORCE ROW LEVEL SECURITY` (the existing in-band
    DO-block pattern, ported);
  - override `_prepare_session(conn, *, namespace)` →
    `SET LOCAL app.scope_namespace = <namespace>`.
- Keep `FLYCANON_PGVECTOR_*` settings mapped onto the framework adapter's params.

### 5.3 `core/services/retrieval/corpus_factory.py`
- Remove the `!= "pgvector"` hard gate. Introduce a name→constructor dispatch:
  - `pgvector` → `RlsPgVectorVectorStore(...)`
  - `qdrant`   → `QdrantVectorStore(url, api_key, collection, vector_size=dim, ...)`
  - `chroma`   → `ChromaVectorStore(collection, client=...)`
  - each wrapped in `TenantScopedVectorStore(...)`.
- `CorpusContext.initialise()` must **also** initialise the dense store
  (`await self.vector_store.initialise()`), so misconfiguration fails at boot, not
  first request. `close()` already closes both.
- Lexical side (`PostgresCorpus`) unchanged.

### 5.4 `core/services/retrieval/retrieval_service.py`
- `_ScopedVectorStore`: **delete the probe**; call the explicit scoped API
  `await inner.search(qvec, top_k=top_k, tenant_id=..., workspace_id=...)`. It
  remains a per-request binder presenting the scope-less `_VectorStoreLike.search`
  to `HybridRetriever`. `_ScopedCorpus` (Postgres lexical) is unchanged.

### 5.5 `core/services/retrieval/index_service.py`
- `replace_for_source`: drop the hard-coded `namespace="default"` on
  `VectorDocument` (the scope wrapper sets it). Replace `_upsert_vectors`
  signature-probe with a direct
  `await vector_store.upsert(docs, tenant_id=..., workspace_id=...)`.
- `remove_for_source`: **purge vectors explicitly.** Resolve the source's chunk
  ids (scoped, via an injected `ChunkRepository`, before the canonical rows are
  removed) and call `vector_store.delete(chunk_ids, tenant_id=..., workspace_id=...)`.
  Fixes a real vector leak (present even for pgvector today). Exact call ordering
  verified against the source-delete CQRS handler during implementation.

### 5.6 Config (`config.py`)
- Add backend-connection settings: `qdrant_url`, `qdrant_api_key`,
  `qdrant_collection`, `chroma_collection`, `chroma_url`/host (as needed),
  plus `pgvector_hnsw_ef_search`. Update the `vector_store` field description
  (no longer "pgvector only"). Validate dimension is set for all backends.

### 5.7 Migration
- Add `migrations/versions/00XX_drop_legacy_chunk_vectors.py`: drop the legacy
  `canon_chunk_vectors` table + its `(tenant_id, workspace_id)` RLS policy (the
  framework adapter recreates the namespace-centric table at boot; re-index
  repopulates). Document that a **re-index is required** after deploy. Other
  `canon_*` tables keep their existing RLS untouched.

### 5.8 Docs & surface
- Update `docs/architecture.md`, `env_template`, `QUICKSTART.md`,
  `docker-compose.yml` (optional qdrant/chroma services for local dev), the
  `config.py` descriptions, and the `/api/v1/version` `VersionInfo` DTO that
  asserts pgvector-only. Regenerate `openapi.json` if the surface changes.

## 6. Testing strategy

- **Framework:** unit (mocked SDK) + `integration`-marked Testcontainers for
  pgvector; hermetic in-memory tests for the scope layer.
- **flycanon:**
  - Port `tests/unit/test_pgvector_scope_isolation.py` to assert the **explicit
    scoped signatures** (required `tenant_id`/`workspace_id`) on the scoped store.
  - **Cross-scope isolation test per backend** (foreign scope returns nothing) —
    via the in-memory fake for hermetic unit coverage, and Testcontainers
    (Qdrant `qdrant/qdrant`, Chroma `chromadb/chroma`, Postgres+pgvector) under
    the `integration` marker.
  - Factory dispatch test: each backend name resolves to the right wrapped store;
    unknown name fails loud.
  - `remove_for_source` purges vectors (assert delete called with chunk ids).
- Pinecone (not in v1 scope) stays covered by the framework's existing mocks.

## 7. Risks / gotchas

- **Silent isolation bypass** — eliminated by D5 (explicit fail-loud port) + the
  mandatory cross-scope tests.
- **`chunk_id` parity** — dense `document.id` MUST equal `canon_chunks.id`
  (RRF + `corpus.get_chunks` rehydration key on it). Adapters preserve ids verbatim.
- **Eager init gap** — fixed by initialising the dense store in
  `CorpusContext.initialise()` (§5.3).
- **Dimension mismatch** — each adapter creates its collection/index at
  `embedding_dimensions`; a startup mismatch should fail loud (assert in
  `initialise()` where the backend exposes the existing dim).
- **Vector leak on delete** — fixed by §5.5.
- **Legacy table reshape** — handled by the §5.7 migration + re-index (safe
  because the projection is derived).
- **Chroma multi-condition `where`** — the framework adapter's metadata `where`
  merges keys without `$and`; isolation relies on `namespace` (single key), which
  is correct. Extra metadata filters are best-effort only.

## 8. Build / release sequence

1. Framework branch `feat/pgvector-and-scoped-vector-store`: implement §4 (TDD),
   green tests, lint/type-check, commit.
2. Point flycanon at the editable framework clone; implement §5 (TDD), green
   tests end-to-end.
3. Merge framework branch → `main`, bump to `26.05.31`, tag `v26.05.31`, push.
4. Re-pin flycanon to `tag = "v26.05.31"`, re-run full suite, commit.
5. Update CHANGELOGs in both repos.
