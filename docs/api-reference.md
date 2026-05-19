<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **API reference**

</div>

---

The OpenAPI document is the canonical source -- visit `/openapi.json`
or the Swagger UI at `/docs` on a running instance. This page is the
human-readable catalogue.

## Sources

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sources` | Submit a source. Body: `SubmitSourceJsonPayload` (base64 bytes via `content_base64` **or** `uri` to fetch). Default sync 201 returns `SourceRecord`; add `?mode=async` for the queued path (returns `IngestJob`, see `/api/v1/jobs/{id}`). Optional `?callback_url=…` fires a webhook on terminal state. Same-content submissions dedup on `content_sha256`. |
| `POST` | `/api/v1/sources:bulk` | Bulk-submit an array of sources. Returns per-item `BulkSourceResult`s. |
| `PUT`  | `/api/v1/sources/{id}` | Replace an existing source's content in place. Body: `SubmitSourceJsonPayload`. |
| `GET`  | `/api/v1/sources` | Paginated list. Query: `status`, `kind` (csv), `limit`, `offset`. |
| `GET`  | `/api/v1/sources/{id}` | Fetch a single source. 404 → `resource_not_found` (RFC 7807). |

## Knowledge

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/knowledge` | Create item + version 1. Body: `CreateKnowledgeRequest`. Returns the new version. |
| `GET`  | `/api/v1/knowledge` | Paginated list. Query: `status`, `domain`, `jurisdiction`, `limit`, `offset`. |
| `GET`  | `/api/v1/knowledge/{id}` | Pointer view. 404 -> `knowledge_item_not_found`. |
| `PUT`  | `/api/v1/knowledge/{id}` | Append a new version. Body: `UpdateKnowledgeRequest`. |
| `POST` | `/api/v1/knowledge/{id}:supersede` | Body: `SupersedeKnowledgeRequest`. |
| `POST` | `/api/v1/knowledge/{id}:retire` | Body: `RetireKnowledgeRequest`. |
| `GET`  | `/api/v1/knowledge/{id}/history` | Full version chain (oldest first). |
| `GET`  | `/api/v1/knowledge/{id}/diff` | Unified diff between two versions. Query: `from_version`, `to_version`. |
| `GET`  | `/api/v1/knowledge/{id}/provenance` | Citation graph for the current version. Query: `version` (optional explicit). |
| `GET`  | `/api/v1/knowledge/{id}/relations` | List `(outgoing, incoming)` typed edges for the item. |
| `POST` | `/api/v1/knowledge/{id}/relations` | Add a typed edge. Body: `CreateRelationRequest`. |
| `DELETE` | `/api/v1/knowledge/{id}/relations/{relation_id}` | Remove an edge. |
| `GET`  | `/api/v1/knowledge:graph` | Whole-canon graph view (JSON or `accept: text/vnd.mermaid` for Mermaid). Query: `domain`, `kind`, `include_sources`. |
| `GET`  | `/api/v1/knowledge:stale` | Per-item staleness scores. Compares published items against fresh sources via cosine. |
| `POST` | `/api/v1/knowledge:detect-conflicts` | Pairwise conflict scan. Returns confirmed conflict pairs as `CandidateRow`s + auto-creates `conflicts_with` edges. |

## Candidates

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/candidates:propose` | Run the consolidation LLM against a source. Body: `ProposeCandidateRequest`. Returns the list of proposed candidate records. |
| `GET`  | `/api/v1/candidates` | Paginated list. Query: `status`, `source_id`, `domain`, `limit`, `offset`. |
| `GET`  | `/api/v1/candidates/{id}` | Fetch a single candidate. |
| `POST` | `/api/v1/candidates/{id}:accept` | Materialise as a knowledge version. Body: `AcceptCandidateRequest`. |
| `POST` | `/api/v1/candidates/{id}:reject` | Discard with a reason. Body: `RejectCandidateRequest`. |

## Query

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/search` | Hybrid retrieval (BM25 + vector + RRF + optional rerank + optional query expansion). Body: `SearchRequest`. |
| `POST` | `/api/v1/query`  | RAG answer with citations. Body: `AnswerRequest`. |
| `POST` | `/api/v1/query/stream` | Same as `/query` but streams tokens as Server-Sent Events. |
| `POST` | `/api/v1/query/suggest` | Suggest follow-up questions for an existing answer. Body: `AnswerRequest`. |

## Conversations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/conversations` | Start a new conversation. Body: `CreateConversationRequest`. Returns the conversation id. |
| `GET`  | `/api/v1/conversations/{id}` | Fetch conversation header + last N turns. The `summary` field is derived from the turn rows on demand (race-free across concurrent turn appends). |
| `POST` | `/api/v1/conversations/{id}/turn` | Submit a user turn; returns the assistant answer with citations. Body: `CreateTurnRequest`. Two parallel POSTs serialise via `UNIQUE(conversation_id, turn_index)` with a bounded retry loop. |

## Async ingest jobs

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/jobs` | Paginated list. Query: `status` (csv), `limit`, `offset`. |
| `GET`  | `/api/v1/jobs/{id}` | Job header — `status`, `attempts`, `source_id` once succeeded, `error_code`/`error_message` on failure. |
| `GET`  | `/api/v1/jobs/{id}/stream` | Server-Sent Events feed of job events (cursor-based; resume with `?after_id=N`). |

## Billing

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/billing` | Aggregated cost report. Query: `group_by` (csv of `date`/`model`/`agent_name`/`actor`), `actor`, `since`, `until`. |
| `GET`  | `/api/v1/billing/events` | Per-call drill-down (correlation id + subject + latency). Query: `actor`, `agent_name`, `since`, `until`, `limit` (1-500), `offset`. |
| `GET`  | `/api/v1/billing/summary` | Rolling-window snapshot (24h / 7d / 30d) with top model + top actor per window. Query: `actor`. |
| `GET`  | `/api/v1/billing/top` | Top-N consumers by cost. Query: `dimension` (`model` / `agent_name` / `actor`), `since`, `until`, `limit` (1-100). |
| `GET`  | `/api/v1/billing/by-subject` | Cost attribution per `(subject_kind, subject_id)`. Query: `subject_kind`, `since`, `until`, `limit` (1-200). |
| `GET`  | `/api/v1/billing/latency` | p50 / p95 / p99 latency per group. Query: `group_by` (csv of `model` / `agent_name` / `actor`), `since`, `until`. |

## Inventory

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/stats` | One-shot corpus + queue + cost-stream snapshot (sources, knowledge items, versions, candidates, chunks, ingest jobs, cost headline). |

## Taxonomy

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/taxonomy` | Flat list of every node, ordered by depth. |
| `POST` | `/api/v1/taxonomy/nodes` | Append a node. Body: `CreateTaxonomyNodeRequest`. |

## Audit

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/audit` | Paginated audit-log view. Query: `subject_id`, `subject_kind`, `event_type`, `limit`, `offset`. |

## Service

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/version` | `VersionInfo` -- service identity, models, retrieval / EDA backends. |
| `GET`  | `/actuator/health` | Aggregated health (database + EDA). |
| `GET`  | `/actuator/health/readiness` | Readiness probe for K8s. |
| `GET`  | `/actuator/health/liveness` | Cheap liveness probe. |
| `GET`  | `/actuator/metrics` | Prometheus-format metrics. |
| `GET`  | `/openapi.json` | The full OpenAPI 3 document. |
| `GET`  | `/docs` | Swagger UI. |
| `GET`  | `/redoc` | ReDoc UI. |
| `GET`  | `/admin/` | pyfly admin dashboard (read-only by default). |

## Error responses

Every 4xx / 5xx response is an RFC 7807
`application/problem+json` document. The shape:

```json
{
  "type": "https://flycanon.dev/problems/<slug>",
  "title": "Human-readable summary",
  "status": 409,
  "code": "knowledge_item_already_retired",
  "detail": "knowledge item 'add55fda-…' is already retired",
  "extensions": { "item_id": "add55fda-…" }
}
```

Programmatic clients dispatch on `code` (stable slug); the human-readable
`title` and `detail` are translation-friendly but not API contract.
Domain-specific extensions land under `extensions` — e.g. `item_id` for
knowledge errors, `candidate_id` for candidate errors, `attempted_version`
for version conflicts.

### Status-code catalogue

| Status | Code slug | When |
|--------|-----------|------|
| 400 | `invalid_request` | Pydantic validation, missing-field `ValueError` raised inside a controller helper. |
| 404 | `resource_not_found` | Generic missing resource (knowledge item, source, conversation, candidate, job). |
| 404 | `knowledge_item_not_found` / `knowledge_version_not_found` / `candidate_not_found` | Typed not-found variants for callers that prefer the specific slug. |
| 409 | `knowledge_item_already_retired` | `:retire` against a retired item, or losing the atomic claim against a concurrent retire. |
| 409 | `knowledge_version_conflict` | Concurrent `PUT /knowledge/{id}` updates collided on `UNIQUE(item_id, version)`. Extensions: `attempted_version`. |
| 409 | `invalid_supersede_target` | `:supersede` against a non-existent / retired target, OR losing the atomic claim against a concurrent supersede. |
| 409 | `candidate_already_decided` | Two operators accepted / rejected the same candidate; the loser sees this. |
| 409 | `relation_already_exists` | `(from, to, kind)` UNIQUE collision on `POST /knowledge/{id}/relations`. |
| 415 | `unsupported_source_kind` | Binary normaliser doesn't recognise the bytes. |
| 422 | `empty_source` / `corrupt_source` | Loader produced no text / pre-flight rejected the file. |
| 422 | `invalid_relation` | Self-relation or unknown target on a relation add. |

### Concurrent-operation conflicts

These 409s are the surface contract for the atomic-claim plumbing in
[concurrency.md](concurrency.md). Two operators clicking the same
`:retire` / `:supersede` / `:accept` simultaneously produce exactly one
success and one typed 409 — never two writes, never a generic 500.
