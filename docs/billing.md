<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Billing + cost stream**

</div>

---

Every LLM call routed through `FireflyAgent` records one
`canon_cost_events` row at completion time. The row carries the
breadcrumbs ops teams need to answer four questions:

* **What did we spend?**            → aggregated report (`GET /api/v1/billing`).
* **What just happened?**           → per-call drill-down (`GET /api/v1/billing/events`).
* **How are we trending?**          → rolling-window snapshot (`GET /api/v1/billing/summary`).
* **Who spent it / where?**         → `GET /api/v1/billing/top`, `GET /api/v1/billing/by-subject`.
* **How slow was it?**              → `GET /api/v1/billing/latency`.

All six endpoints are read-only. They project the same
`canon_cost_events` table; no separate billing-side store.

## Recorded fields

Every cost event carries:

| Field            | Purpose |
|------------------|---------|
| `agent_name`     | Which agent ran (`flycanon-answerer`, `flycanon-conflict-judge`, …). |
| `model`          | The model identifier (`anthropic:claude-sonnet-4-6`, …). |
| `input_tokens` / `output_tokens` / `total_tokens` | Token counters captured from the agent's usage block. |
| `cost_usd`       | Decimal (6-place) USD spent. Serialised as a string in JSON to preserve precision. |
| `latency_ms`     | End-to-end latency of the call. |
| `actor`          | Caller identity -- audit metadata only since Plan 4 (partitioning moved to `(tenant_id, workspace_id)` on the headers). |
| `tenant_id` / `workspace_id` | Scope columns supplied by `X-Tenant-Id` + `X-Workspace-Id` on every cost-recording call. Aggregations group on this pair. |
| `correlation_id` | W3C correlation id from the originating request -- pivot back to the audit log. |
| `subject_kind` / `subject_id` | Optional breadcrumb: `source`/`source_id`, `knowledge_item`/`item_id`, etc. Powers `/by-subject`. |
| `occurred_at`    | UTC timestamp. |

## Endpoints

### `GET /api/v1/billing` -- aggregated report

Query: `group_by` (csv of `date` / `model` / `agent_name`),
`since`, `until`. Scope is supplied via the `X-Tenant-Id` +
`X-Workspace-Id` headers (Plan 4); the legacy `actor` Query param
is retired.

```json
{
  "rows": [
    {
      "group": { "date": "2026-05-18", "model": "anthropic:claude-sonnet-4-6" },
      "input_tokens": 12345,
      "output_tokens": 6789,
      "total_tokens": 19134,
      "cost_usd": "0.04231",
      "calls": 12
    }
  ],
  "total_cost_usd": "0.04231",
  "total_calls": 12
}
```

### `GET /api/v1/billing/events` -- per-call drill-down

Query: `agent_name`, `since`, `until`, `limit` (1-500), `offset`.
Scope is supplied via the request headers (Plan 4).

```json
{
  "rows": [
    {
      "id": 1234,
      "agent_name": "flycanon-answerer",
      "model": "anthropic:claude-sonnet-4-6",
      "input_tokens": 1024,
      "output_tokens": 256,
      "total_tokens": 1280,
      "cost_usd": "0.00420",
      "latency_ms": 842,
      "actor": "u-1",
      "correlation_id": "01HV...",
      "subject_kind": "knowledge_item",
      "subject_id": "ki-1",
      "occurred_at": "2026-05-18T17:00:00Z"
    }
  ],
  "limit": 50,
  "offset": 0
}
```

Pivot from the aggregated row into this list when a spend spike needs
forensic detail (which correlation id, which subject, how slow).

### `GET /api/v1/billing/summary` -- rolling-window snapshot

Three windows always populated (zero rows when no data, not 404):

```json
{
  "generated_at": "2026-05-18T17:00:00Z",
  "last_24h": {
    "since": "2026-05-17T17:00:00Z",
    "calls": 142,
    "input_tokens": 312000,
    "output_tokens": 78000,
    "total_tokens": 390000,
    "cost_usd": "4.21",
    "top_model": "anthropic:claude-sonnet-4-6",
    "top_model_cost_usd": "3.82",
    "top_workspace_id": "ws-eu",
    "top_workspace_cost_usd": "1.95"
  },
  "last_7d":  { /* ... */ },
  "last_30d": { /* ... */ }
}
```

The summary is always scoped to the tenant + workspace from the
request headers; the legacy `top_actor` field is retired.

### `GET /api/v1/billing/top` -- top-N consumers

Query: `dimension` (one of `model` / `agent_name`), `since`,
`until`, `limit` (1-100, default 10). Scope is supplied via the
request headers (Plan 4 -- `actor` is no longer a valid dimension).

```json
{
  "dimension": "model",
  "rows": [
    { "dimension": "model", "value": "anthropic:claude-sonnet-4-6", "input_tokens": 312000, "output_tokens": 78000, "total_tokens": 390000, "cost_usd": "3.82", "calls": 120 }
  ]
}
```

`dimension` outside the allowed set returns RFC 7807
`bad_request_exception` -- the controller validates up-front.

### `GET /api/v1/billing/by-subject` -- cost attribution

Query: `subject_kind`, `since`, `until`, `limit` (1-200, default 20).

```json
{
  "rows": [
    {
      "subject_kind": "source",
      "subject_id": "src-...",
      "input_tokens": 24000,
      "output_tokens": 6000,
      "total_tokens": 30000,
      "cost_usd": "0.21",
      "calls": 8
    }
  ]
}
```

Rows without a populated subject pair are excluded -- the caller-side
recorder must pass `subject_kind` / `subject_id` for the call to appear
here.

### `GET /api/v1/billing/latency` -- p50 / p95 / p99

Query: `group_by` (csv of `model` / `agent_name`), `since`,
`until`. Scope is supplied via the request headers (Plan 4).

```json
{
  "rows": [
    {
      "group": { "model": "anthropic:claude-sonnet-4-6" },
      "count": 120,
      "avg_ms": 842,
      "p50_ms": 720,
      "p95_ms": 1840,
      "p99_ms": 2900,
      "max_ms": 4210
    }
  ]
}
```

Percentiles are computed in Python (linear interpolation) so the
contract works on both Postgres and SQLite -- no `percentile_cont`
required.

## Operational tips

* **Budget alerts.** Combine `/summary.last_24h.cost_usd` with a
  scheduled poll + your alerting stack. The endpoint is cheap enough
  to hit every minute.
* **Hot-spot triage.** A `/summary` spike points at the dominant
  model; `/top?dimension=model` confirms; `/events` pivots into the
  offending calls (correlation id + subject id).
* **Attribution.** Always set `subject_kind` / `subject_id` when
  recording cost. Ingest cost should attribute to
  `source` / `<source_id>`; consolidation runs should attribute to
  `knowledge_item` / `<item_id>`. `/by-subject` is otherwise useless.

## Pluggable cost middleware

`CostService.record` is provider-agnostic. The roadmap calls for
fanning in external-provider costs (Cohere / Voyage rerank, OpenAI
embeddings) by emitting `agent.completed` from the agent middleware
that owns each call -- no schema or endpoint changes required.
