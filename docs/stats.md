<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Corpus inventory**

</div>

---

`GET /api/v1/stats` is the one-shot snapshot a status dashboard
needs: a single round-trip that returns every counter ops cares about
without scraping six different endpoints.

The scope is intentionally tight to flycanon's mission -- knowledge
artefacts, the ingest queue, and the LLM cost stream feeding both.
Counts that belong to other services (sessions, billing customers,
external integrations) don't live here.

## Endpoint

```
GET /api/v1/stats
```

No query parameters. The handler hits five small `SELECT COUNT(*)`
queries (one per table) plus the `canon_cost_events` rollup. Cheap to
poll; safe for live dashboards.

## Response (`CorpusStats`)

```json
{
  "generated_at": "2026-05-18T17:00:00Z",
  "sources": {
    "total": 142,
    "by_kind":   { "pdf": 87, "docx": 36, "html": 12, "markdown": 7 },
    "by_status": { "ingested": 140, "failed": 2 },
    "total_bytes": 187200000
  },
  "knowledge_items": {
    "total": 64,
    "by_status": { "published": 52, "draft": 8, "superseded": 3, "retired": 1 },
    "by_domain": { "compliance": 18, "process": 24, "finance": 14, "security": 8 }
  },
  "knowledge_versions": 89,
  "candidates": {
    "total": 21,
    "by_status": { "proposed": 7, "accepted": 12, "rejected": 2 }
  },
  "chunks": {
    "total": 9842,
    "embedded": 9840,
    "embedded_pct": 99.98
  },
  "ingest_jobs": {
    "total": 38,
    "by_status": { "completed": 36, "failed": 1, "running": 1 },
    "avg_attempts": 1.05
  },
  "cost": {
    "total_events": 942,
    "cost_usd_24h": "4.21",
    "cost_usd_30d": "87.43"
  }
}
```

## Field guide

* **`sources.by_kind`** -- one of the routing-matrix labels
  (`pdf` / `docx` / `xlsx` / `pptx` / `html` / `markdown` /
  `image` / `archive` / `email` / ...).
* **`sources.by_status`** -- `pending` / `ingested` / `failed` /
  `superseded`.
* **`knowledge_items.by_status`** -- `draft` / `published` /
  `superseded` / `retired`.
* **`chunks.embedded_pct`** -- percentage of chunks with a non-NULL
  embedding vector. A drop below 100 % is the leading indicator that
  the embedding provider is failing.
* **`ingest_jobs.by_status`** -- `queued` / `running` / `completed` /
  `failed` / `cancelled`. `avg_attempts` > 1 means jobs are retrying.
* **`cost`** -- the headline rollup. Detailed cost drill-downs live
  in [billing.md](billing.md).

## Use cases

| Dashboard tile             | Source                                                                          |
| -------------------------- | ------------------------------------------------------------------------------- |
| "Documents indexed"        | `sources.total` + `sources.by_kind` distribution                                 |
| "Published knowledge"      | `knowledge_items.by_status.published`                                            |
| "Inbox" badge              | `candidates.by_status.proposed`                                                  |
| "Retrieval coverage"       | `chunks.embedded_pct`                                                            |
| "Queue health"             | `ingest_jobs.by_status` + `ingest_jobs.avg_attempts`                             |
| "Spend today / 30 days"    | `cost.cost_usd_24h` / `cost.cost_usd_30d`                                        |

## Cost vs. stats split

`GET /api/v1/stats` exposes the **headline cost rollup** so a single
panel can render alongside the corpus counters. For the deep cost
surfaces (top consumers, latency, subject attribution, drill-down),
go to `/api/v1/billing/*` -- see [billing.md](billing.md).
