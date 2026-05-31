# Scale + performance -- flycanon

Capacity-planning reference for the **flycanon** Operational Knowledge
Repository. Aimed at the SRE who has to size Postgres, dial the
HNSW / RRF knobs, and decide when (and whether) to introduce
per-tenant partitioning.

For the cross-link map:

* [`architecture.md`](architecture.md) -- layered structure +
  invariants the numbers below depend on.
* [`deployment.md`](deployment.md) -- the actual env-var surface +
  Postgres role split.
* [`consumers.md`](consumers.md) -- downstream callers + their
  expected QPS.
* [`concurrency.md`](concurrency.md) -- worker dispatch + ingest
  lease semantics.
* [`operations-runbook.md`](operations-runbook.md) -- on-call
  procedures, probes, incident response.

---

## 1. Workload model

flycanon is sized for the following **moderate-to-high** envelope.
A deployment past these envelopes should consult section 6 before
scaling further.

| Dimension                          | Target envelope         |
|------------------------------------|-------------------------|
| Tenants                            | 10s -- 100s             |
| Workspaces per tenant              | 100s                    |
| Sources per workspace              | 1000s                   |
| Chunks per workspace (`canon_chunks`) | 100K -- 10M          |
| Dense embeddings per workspace (`canon_chunk_vectors`) | matches chunks |
| Read QPS (`/search`, `/query`)     | 50 -- 500               |
| Write QPS (`/sources`, agent handoff) | 10 -- 50             |
| RAG answer p95 budget              | 5s                      |
| Hybrid search p99 budget           | 200ms                   |

These envelopes assume the default **Tier-A** vector layout (single
shared `canon_chunk_vectors` table + global HNSW). Beyond ~500K
chunks for a single hot tenant the
[partition_admin module docstring](../src/flycanon/core/services/retrieval/partition_admin.py)
recommends promoting that tenant to Tier-B (section 6).

---

## 2. Storage layout

Per-table sizing notes; row counts dominate the planning exercise.
All tables sit on the same Postgres cluster.

### `canon_sources`

* Bounded by ingest rate * retention.
* One row per accepted artefact -- no bytes stored inline
  (`content_sha256` plus normalised metadata).
* Indexes: status, kind, content_sha256 (unique-when-not-null);
  composite `(tenant_id, workspace_id)`.

### `canon_chunks`

* Bounded by `sources * avg chunks per source`.
* Default chunker: `FLYCANON_CHUNK_SIZE_TOKENS=1200`,
  `FLYCANON_CHUNK_OVERLAP_TOKENS=150`, `FLYCANON_CHUNK_STRATEGY=paragraph`.
* Carries a Postgres-generated `tsv tsvector` column (migration
  `0003_bm25_tsv`) used by BM25; the `GIN` index on `tsv` is the
  full-text projection.
* Indexes: source_id, `(source_id, index_in_source)`,
  `(tenant_id, workspace_id)`, scope+source composite (migration
  `0009_embeddings_scope`).

### `canon_chunk_vectors`

* Same row count as `canon_chunks` -- one dense projection per chunk.
* Per-row width = `vector(<dim>)` payload + ~80 B of bookkeeping
  (id, namespace, scope, metadata).
* Default `FLYCANON_EMBEDDING_DIMENSIONS=1536`
  (`openai:text-embedding-3-small`). That's **6.1 KiB per row** for
  the vector payload alone (1536 * 4 B float). Plan ~6.5 KiB total
  per row including bookkeeping.
* For 10M chunks at 1536 dims: **~62 GiB of vector data** plus the
  HNSW graph (section 3).
* Cosine distance (`vector_cosine_ops`).

### `canon_knowledge_items`

* Bounded by accepted candidates. The consolidation pipeline
  emits ~one `KnowledgeItem` per coherent topic.
* Small (typically << 1% of chunks).

### `canon_knowledge_versions`

* Append-only; bounded by version churn rate per item.
* Each item supersedes-by-version forms a chain (see
  [`pipeline.md`](pipeline.md)).
* No automatic compaction; deprecate via the supersede flow.

### `canon_audit_events`

* High write volume -- every state change writes one row.
* Indexed by `(event_type)`, `(subject_id)`, `(subject_kind)`,
  `(actor)`, `(occurred_at)`, and `(subject_kind, subject_id)`.
* Retention sweep is currently **manual** (see section 11). A
  scheduled reaper analogous to flyradar's `RetentionWorker` is a
  follow-up; in the meantime, operators should expect linear growth.

### Other tables

* `canon_workspaces` -- one row per workspace; bounded by tenant
  count * workspaces-per-tenant.
* `canon_conversations` / `canon_conversation_turns` -- bounded by
  `/api/v1/conversations` traffic; see
  [`conversations.md`](conversations.md).
* `canon_ingest_jobs` / `canon_ingest_job_events` -- bounded by
  async ingest queue depth; see [`async-ingest.md`](async-ingest.md).
* `canon_cost_events` -- one row per LLM call; bounded by traffic.
  See [`billing.md`](billing.md).

---

## 3. pgvector tuning

The PgvectorStore initialisation is in
[`src/flycanon/core/services/retrieval/pgvector_store.py`](../src/flycanon/core/services/retrieval/pgvector_store.py).
The HNSW index is created once on first boot via
`_initialise_schema` with the parameters below.

### HNSW build params

| Knob                                      | Default | Env override                              | Where                  |
|-------------------------------------------|---------|-------------------------------------------|------------------------|
| `m` (graph degree)                        | 16      | `FLYCANON_PGVECTOR_HNSW_M`                | pgvector_store.py:99   |
| `ef_construction` (build candidate list)  | 64      | `FLYCANON_PGVECTOR_HNSW_EF_CONSTRUCTION`  | pgvector_store.py:100  |

These are baked into the index at create time; changing them
afterwards requires `REINDEX` (or `DROP INDEX` + recreate).

### Runtime ef_search

`ef_search` is set **per transaction** via `SET LOCAL hnsw.ef_search = 200`
inside `PgVectorVectorStore.search` (line 288). 200 is the value
that ships -- baked into the `_HNSW_EF_SEARCH` constant, not an
env knob. Trade-off recall vs. latency:

* `ef_search = 40` (pgvector default) -- ~1ms but recall drops.
* `ef_search = 200` (flycanon ships) -- ~2-3ms; recall is
  competitive with brute force on the default `m=16` index.
* `ef_search = 400` -- ~5ms; for high-recall corpora. Bumping
  requires a code change today (`_HNSW_EF_SEARCH` is a constant);
  a `FLYCANON_PGVECTOR_HNSW_EF_SEARCH` env knob is a follow-up.

### Distance function

Cosine (`vector_cosine_ops`). The query renders the score as
`1 - (embedding <=> :query_embedding)` so callers see a
similarity, not a distance.

### Widening factor

`PgVectorVectorStore.search` pulls `top_k * widening_factor`
candidates from the index (default `widening_factor=5`,
`_DEFAULT_WIDENING_FACTOR` in pgvector_store.py:62) so the
downstream RRF / cross-encoder rescores a wider pool. The list is
trimmed to `top_k` before returning. Not currently exposed as an
env knob -- callers can override via the function signature.

### Embedding dimensions

`FLYCANON_EMBEDDING_DIMENSIONS=1536` is the default
(`openai:text-embedding-3-small`). The `pgvector` extension stores
fixed-width vectors; changing dimensions requires re-embedding
every row and rebuilding the index. Pick once at deployment time.

### Memory cost of HNSW

HNSW lives in RAM for hot queries. Postgres relies on the OS page
cache, so the working set you need to keep "warm" is
`vectors + graph`:

* Vector payload: `4 B * dim` per row (1536 dims = 6.1 KiB).
* Graph: roughly `8 B * m * 2` per row at `m=16` = ~256 B per row
  for inbound + outbound edges.
* Total: **~6.4 KiB per row warm-memory budget** at the defaults.

For 10M chunks: budget ~64 GiB of RAM for the page cache to keep
the HNSW index hot. Disk footprint is roughly the same. Below
that, expect cold-cache spikes on the 200ms p99 budget.

---

## 4. Hybrid retrieval (RRF)

Hybrid retrieval fuses BM25 (Postgres FTS) and dense (pgvector)
ranks via Reciprocal Rank Fusion. The fusion is driven by the
agentic `HybridRetriever`; flycanon wires it via
[`retrieval_service.py`](../src/flycanon/core/services/retrieval/retrieval_service.py)
and [`corpus_factory.py`](../src/flycanon/core/services/retrieval/corpus_factory.py).

### Knobs

| Setting                          | Default | Env                                |
|----------------------------------|---------|------------------------------------|
| Final top-k returned to caller   | 10      | `FLYCANON_RETRIEVAL_TOP_K`         |
| Per-leg candidate window         | 30      | `FLYCANON_RETRIEVAL_PER_QUERY_K`   |
| RRF `k` parameter                | 60      | `FLYCANON_RETRIEVAL_RRF_K`         |
| Reranker top-N (cross-encoder)   | 20      | `FLYCANON_RERANKER_TOP_N`          |
| Reranker model id                | unset   | `FLYCANON_RERANKER_MODEL`          |
| Query expansion enabled          | false   | `FLYCANON_QUERY_EXPANSION_ENABLED` |
| Query expansion paraphrase count | 3       | `FLYCANON_QUERY_EXPANSION_N`       |

### RRF mechanics

RRF score per doc = sum over legs of `1 / (k + rank_in_leg)`. The
`k=60` default is the standard from the original RRF paper; higher
`k` flattens the curve (less aggressive rank decay). Bump `k` if
you find the top of one leg dominating the fused list; lower it
if you want top-1 of each leg surfaced strongly.

### Per-leg widening

The retriever widens each leg to `retrieval_per_query_k=30` before
fusion. With `retrieval_top_k=10` that's a 3x widening: enough
slack for RRF to reorder. Bump `per_query_k` when callers see
relevant hits missing from the final 10 but visible in either
single-leg search.

### Optional reranker

`FLYCANON_RERANKER_MODEL` adds a cross-encoder pass on the
post-fusion top-N (`FLYCANON_RERANKER_TOP_N=20`). Costs one
extra provider call per query. Off by default; turn on for corpora
where missed citations cost more than ~200ms of latency.

### Optional query expansion

`FLYCANON_QUERY_EXPANSION_ENABLED=true` asks the answer model to
paraphrase the user's query N times, runs each through the
retriever, and RRF-fuses the N+1 result lists. Costs one LLM call
per query. Off by default; turn on for "data retention" vs.
"record disposal"-style vocabulary gaps.

---

## 5. BM25 tuning

Postgres-native BM25 on `canon_chunks.tsv` (a `GENERATED ALWAYS
AS (to_tsvector(...))` column from migration `0003_bm25_tsv`).
A `GIN` index on the column is built once at migration time and
maintained automatically by Postgres on every INSERT / UPDATE of
`canon_chunks.content`.

### Knobs

| Setting                       | Default  | Env                                |
|-------------------------------|----------|------------------------------------|
| Text-search configuration     | `simple` | `FLYCANON_BM25_TEXT_SEARCH_CONFIG` |

* `simple` -- no stemming, no stopwords. Safest default for
  multilingual corpora (the default; see
  [postgres_corpus.py docstring](../src/flycanon/core/services/retrieval/postgres_corpus.py)).
* `english` / `spanish` / etc. -- language-aware stemming. Bumps
  recall for mono-lingual corpora at the cost of cross-language
  hits.

### Index cost

* Build is one-pass at migration time -- linear in
  `sum(length(content))`.
* Runtime BM25 lookups are sub-millisecond on the warm cache; the
  GIN index is hit before the `(tenant_id, workspace_id)`
  selectivity filter.

### Scope filtering

The `(tenant_id, workspace_id)` composite is applied **before** the
`tsv @@ plainto_tsquery` match (see `bm25_search` in
[postgres_corpus.py:140](../src/flycanon/core/services/retrieval/postgres_corpus.py)).
The composite is selective enough post migration `0009` that the
GIN-on-tsv index still gets picked by the planner.

---

## 6. Per-tenant partitioning (Tier-B, dormant)

`canon_chunk_vectors` ships in **Tier-A** layout: a single shared
table with a global HNSW index, scope-filtered by `WHERE tenant_id
= ? AND workspace_id = ?`. This is the default and works
comfortably to **~500K chunks for a single hot tenant** (the
threshold is documented inline in
[partition_admin.py](../src/flycanon/core/services/retrieval/partition_admin.py)).
Past that, the global HNSW starts paying a recall tax because the
scope filter rejects most of the candidate list.

**Tier-B** carves a dedicated partition + per-partition HNSW index
for hot tenants. Tier-B is **dormant** by default -- it requires:

1. A one-time table conversion to `PARTITION BY LIST (tenant_id)`
   with downtime. The full DDL recipe is in the
   [partition_admin.py module docstring](../src/flycanon/core/services/retrieval/partition_admin.py).
2. Per-hot-tenant `promote_tenant_to_partition(engine, tenant_id)`
   (idempotent; creates the partition + per-partition HNSW).
3. `demote_tenant_from_partition(engine, tenant_id)` to merge a
   cooled-down tenant back into the default partition.

### When to promote

* **Empirical threshold**: a tenant whose `canon_chunk_vectors`
  row count crosses ~500K.
* **Symptoms**: dense search p99 climbing past the 200ms budget on
  one tenant's queries even though others stay fast; HNSW recall
  drops measurably (top-1 chunks from BM25 missing from the dense
  leg's top-30).

### When to demote

* When a previously hot tenant has been quiet for long enough that
  the partition no longer pays back its index maintenance cost.
* Routine merging of small partitions back into the default keeps
  the table count manageable; Postgres planner overhead grows with
  partition count.

The `promote_*` / `demote_*` calls are stable and intentional
escape valves; production clusters should expect to live on Tier-A
indefinitely.

---

## 7. RAG answer pipeline

The RAG answer endpoint (`POST /api/v1/query`) is composed of
three stages; the budget below totals to the **5s p95 target**.
Implementation: [`answer_service.py`](../src/flycanon/core/services/query/answer_service.py).

| Stage                | p95 budget | Notes                                                          |
|----------------------|------------|----------------------------------------------------------------|
| Hybrid retrieval     | 200 ms     | pgvector HNSW + BM25 + RRF -- see sections 3-5.                |
| LLM answer call      | 3.5 s      | `FLYCANON_ANSWER_MODEL` (default `anthropic:claude-sonnet-4-6`). Provider-dominated. |
| Citation enrichment  | 100 ms     | Hydrate `chunk_id` set from the retrieval result.              |
| Headroom             | 1.2 s      | Provider variability + connection setup.                       |

### LLM cost dimensions

* `FLYCANON_AGENT_MAX_OUTPUT_TOKENS=8192` -- the per-call output
  ceiling.
* `FLYCANON_ANSWER_MAX_OUTPUT_TOKENS` -- per-stage override.
* `FLYCANON_ANSWER_FALLBACK_MODEL` -- failover model when the
  primary errors. Recommended in production.

### Latency observability

`AnswerService.answer` records `elapsed_ms` per call and logs it
at INFO level (`answer_service.py:154`). The retrieval substage
emits its own `elapsed_ms` from
[`retrieval_service.py:200`](../src/flycanon/core/services/retrieval/retrieval_service.py).
See section 12 for the recommended Prometheus shape.

---

## 8. Connection pools

flycanon uses SQLAlchemy `create_async_engine` with **default**
pool sizing -- no explicit `pool_size` or `max_overflow` argument
is set in
[`_engine.py`](../src/flycanon/models/repositories/_engine.py),
in [`postgres_corpus.py`](../src/flycanon/core/services/retrieval/postgres_corpus.py),
or in [`pgvector_store.py`](../src/flycanon/core/services/retrieval/pgvector_store.py).
That means the active configuration is the SQLAlchemy QueuePool
default of `pool_size=5` + `max_overflow=10` (15 total) **per
engine**.

`pool_pre_ping=True` is set everywhere -- that's the only knob
flycanon overrides today.

### Why three engines

The cached singleton pattern in `build_engine` keeps the
repository tier on one engine, but `PostgresCorpus` and
`PgVectorVectorStore` each build their own. A boot of the
canonical layout produces three pools (rows-tier, BM25 corpus,
vector store) sharing the same Postgres but with independent
connection budgets.

### Sizing guidance

* **Read QPS < 100**: defaults are fine.
* **Read QPS 100-300**: replicas help more than pool bumps; horizontal
  add-a-pod is preferred over raising per-pool limits.
* **Read QPS 300+ on a single replica**: needs explicit pool sizing
  (target ~30 connections per pool). A `FLYCANON_POOL_SIZE` /
  `FLYCANON_POOL_MAX_OVERFLOW` env knob is a follow-up; until it
  lands, operators have to monkey-patch `build_engine` or pin the
  defaults via Postgres `max_connections`.

> Don't forget Postgres has a hard `max_connections` ceiling (the
> stock 100 leaves <85 for the app after Postgres' own workers).
> Use a connection pooler (PgBouncer) if your pod count * total
> pool size exceeds the cluster's headroom.

---

## 9. EDA broker

The active broker is driven by `FLYCANON_EDA_ADAPTER` (see
`config.py:42` and `pyfly.yaml:eda.provider`). All adapters
implement the same `EventPublisher` protocol; flycanon emits the
same envelope shape regardless of broker.

| Adapter   | Throughput envelope        | Notes                                                              |
|-----------|----------------------------|--------------------------------------------------------------------|
| `memory`  | n/a                        | In-process only -- dev / test. No durability.                       |
| `postgres` (default) | ~1000 events/s   | Durable outbox via pyfly + LISTEN/NOTIFY. No extra broker to run.   |
| `redis`   | ~5000 events/s             | Redis Streams; durable but bounded by Redis memory.                 |
| `kafka`   | 10K+ events/s              | Production fan-out at scale. Multi-replica + retention guarantees.  |

* The Postgres outbox is the default because flycanon already runs
  Postgres for persistence (`canon_workspaces`, etc.). No extra
  service to operate.
* For deployments emitting 10K+ events/sec, switch to Kafka. The
  `pyfly.yaml` indirection (`provider: ${FLYCANON_EDA_ADAPTER:postgres}`)
  means the swap is purely a config change.
* See [`eda-events.md`](eda-events.md) for the envelope shape and
  topic catalogue.

---

## 10. Caching strategy

flycanon has **two** in-process caches today; both are
process-local. A Redis-backed shared variant is available as a
pluggable adapter (the `IdempotencyStore` + `_RateLimiter`
Protocols are the integration seams; operators opt in via env var).

### Idempotency store

`InMemoryIdempotencyStore` is wired by default in
[`core/configuration.py:176`](../src/flycanon/core/configuration.py).
The store keeps the agent-tier replay window honest:

* TTL: 24 h (`DEFAULT_IDEMPOTENCY_TTL` in
  [`web/conventions/idempotency.py:53`](../src/flycanon/web/conventions/idempotency.py)).
* Key shape: `(tenant_id, route, key)`.
* Memory budget per entry: ~1 KiB (status, JSON body + a few
  string columns).

**Worked memory budget**: 100 RPS sustained * 24h * 1 KiB ~= 8 GiB.
That's the worst-case upper bound; cleanups happen lazily on
lookup so real footprint is lower. **Not scalable for sustained
high QPS without a Redis backing**; the Protocol is pluggable
(see the Protocol definition in `idempotency.py:109`) so a
production Redis adapter can drop in.

### Per-token rate-limit bucket

`_RateLimiter` in
[`auth/agent_token_service.py:206`](../src/flycanon/core/services/auth/agent_token_service.py)
keeps a 60s sliding window per `token_id`:

* Bucket cap: 10000 timestamps (`deque(maxlen=10000)` in
  `_TokenBucket.__init__`).
* Memory: ~24 B per timestamp + dict overhead. Negligible
  compared to the idempotency store.
* **Process-local**. Multiple replicas multiply the **effective**
  rate -- a `rate_limit_rpm=60` token, when called against three
  replicas, can do up to 180 RPM end-to-end. Set
  `rate_limit_rpm` conservatively for multi-replica deployments,
  or opt into the Redis-backed adapter for a shared counter
  across replicas (the `_RateLimiter` Protocol is the integration
  seam; exception class + status code are wire-stable).

---

## 11. Retention sweep

flycanon **does not** ship an automated retention worker today.
`canon_workspaces.retention_days` is recorded on the workspace row
(see `WorkspaceRepository` and migration `0008_workspaces`) but no
background loop currently consumes it -- the field is a published
intent, not an enforced sweep.

### Workaround until the reaper lands

* High-cardinality tables (`canon_audit_events`,
  `canon_ingest_job_events`, `canon_cost_events`) grow linearly
  with traffic; size headroom accordingly or run periodic
  housekeeping DELETEs out-of-band.
* The consolidation worker
  ([`workers/ingest_worker.py`](../src/flycanon/core/services/workers/ingest_worker.py))
  drains the EDA topic but does NOT purge on retention -- it only
  consolidates inbound material.

### Follow-up

A scheduled retention loop analogous to flyradar's
`RetentionWorker`
([`flyradar/src/flyradar/core/services/workers/retention_worker.py`](../../flyradar/src/flyradar/core/services/workers/retention_worker.py))
is on the roadmap. Until it lands, operators should treat
`retention_days` as a downstream contract (for example, signalling
to the agent-tier handoff that flyradar-pushed sources can be
expired N days after their `created_at`) rather than an enforced
in-flycanon sweep.

---

## 12. Observability for performance

### Structured log fields

The hot-path services already emit per-call durations -- the
following fields are available without adding code:

| Field             | Where                                                                                                | Stage                |
|-------------------|------------------------------------------------------------------------------------------------------|----------------------|
| `elapsed_ms` (retrieval) | [`retrieval_service.py:200`](../src/flycanon/core/services/retrieval/retrieval_service.py)    | Hybrid retrieval     |
| `elapsed_ms` (answer)    | [`answer_service.py:152`](../src/flycanon/core/services/query/answer_service.py)              | RAG answer           |
| `elapsed_ms` (worker)    | [`ingest_worker.py:209`](../src/flycanon/core/services/workers/ingest_worker.py)              | Async ingest         |

These ride on the structured-logging adapter that pyfly's
observability auto-config wires (`pyfly.yaml:observability.enabled`).

### Prometheus metrics

`pyfly.observability` + `pyfly.metrics` are both enabled in
`pyfly.yaml`. The framework exposes the standard FastAPI HTTP
histograms out of the box; recommended **custom** histograms (not
yet emitted -- on the roadmap):

* `flycanon_retrieval_duration_ms` (bucketed: 10, 25, 50, 100,
  200, 500, 1000, 2000).
* `flycanon_pgvector_duration_ms` (bucketed: 1, 2, 5, 10, 25, 50,
  100, 250).
* `flycanon_bm25_duration_ms` (bucketed: 1, 2, 5, 10, 25, 50, 100).
* `flycanon_llm_answer_duration_ms` (bucketed: 100, 250, 500, 1000,
  2500, 5000, 10000).

### Tracing spans

`pyfly.tracing.enabled=true` (`pyfly.yaml`) wires OpenTelemetry.
Today flycanon does not emit custom spans per retrieval leg; a
roadmap item is to annotate `RetrievalService.search`,
`bm25_search`, and `PgVectorVectorStore.search` with explicit spans
so distributed traces from the agent-tier handoff stitch through to
the underlying database calls.

---

## Cross-service performance budgets

The full cross-service budget matrix lives at the end of
flyradar's [`scale-and-performance.md`](../../flyradar/docs/scale-and-performance.md);
duplicated here for the canon-side row reference.

| Operation                  | p95 target | Notes                                  |
|----------------------------|------------|----------------------------------------|
| Canon hybrid retrieval     | 200 ms     | pgvector HNSW + BM25 + RRF             |
| Canon RAG answer           | 5 s        | dominated by LLM provider              |
| Radar discovery validate   | 100 ms     | local-only                             |
| Radar discovery submit     | 200 ms     | local-only                             |
| Radar discovery completion | 30s -- 5min | LLM-bound                              |
| Cross-service handoff      | 500 ms     | radar -> canon HTTP + canon write      |
