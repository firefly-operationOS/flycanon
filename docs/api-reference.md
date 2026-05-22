<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **API reference**

</div>

---

The OpenAPI document is the canonical source -- visit `/openapi.json`
or the Swagger UI at `/docs` on a running instance. This page is the
human-readable catalogue.

## Required headers

Every `/api/v1/*` request **except** `/api/v1/version` requires the
tenant-context headers (Plan 4 conventions adoption):

| Header | Grammar | Notes |
|--------|---------|-------|
| `X-Tenant-Id` | `^[a-z0-9][a-z0-9_-]{0,63}$` | Identifies the calling tenant. |
| `X-Workspace-Id` | same | Workspace within the tenant. |

Missing or malformed headers -> `400 missing_tenant_context`
(RFC 7807 envelope; see _Error responses_ below). The
`X-Correlation-Id` / `X-Request-Id` propagation headers from pyfly
are unchanged.

## Workspace scope enforcement

**Workspace scope is enforced on every read-by-id route.** A caller who
presents a resource id from a different workspace (same tenant or
different tenant) receives `404 resource_not_found`. Applies to both
the user surface and the agent surface (`/api/v1/agent/*`). This is
true for knowledge, sources, candidates, conversations, ingest-jobs,
and any sub-routes that take an id (e.g., `/{id}/history`,
`/{id}/provenance`, `/{id}/relations`).

The 404 is the wire surface of two cooperating gates: the handler /
repository filter on `(tenant_id, workspace_id)` and, on Postgres,
row-level security (see [architecture.md -> Row-level security](architecture.md#row-level-security)).
Either alone would return 404; defence-in-depth keeps the surface
consistent even when a future repository forgets the WHERE clause.

## Sources

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sources` | Submit a source. Body: `SubmitSourceJsonPayload` (base64 bytes via `content_base64` **or** `uri` to fetch). Default sync 201 returns `SourceRecord`; add `?mode=async` for the queued path (returns `IngestJob`, see `/api/v1/ingest-jobs/{id}`). Optional `?callback_url=…` fires a webhook on terminal state. Same-content submissions dedup on `(tenant_id, workspace_id, content_sha256)`. |
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

Renamed in Plan 4 (was `/api/v1/jobs/*`) to parallel flyradar's
`/api/v1/discovery-jobs/*`.

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/ingest-jobs` | Paginated list. Query: `status` (csv), `limit`, `offset`. |
| `GET`  | `/api/v1/ingest-jobs/{id}` | Job header — `status`, `attempts`, `source_id` once succeeded, `error_code`/`error_message` on failure. |
| `GET`  | `/api/v1/ingest-jobs/{id}/stream` | Server-Sent Events feed of job events (cursor-based; resume with `?after_id=N`). |

## Workspaces

CRUD over `canon_workspaces` (Plan 2 table). All five endpoints
require the standard tenant headers; list / get / update / close
scope by `X-Tenant-Id`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/workspaces` | Create. Body: `WorkspaceCreate`. Returns `WorkspaceSummary`. |
| `GET`  | `/api/v1/workspaces` | List within tenant. Query: `status`, `limit`, `offset`. |
| `GET`  | `/api/v1/workspaces/{id}` | Fetch one. 404 → `resource_not_found`. |
| `PATCH` | `/api/v1/workspaces/{id}` | Sparse update. Body: `WorkspaceUpdate`. |
| `POST` | `/api/v1/workspaces/{id}:close` | Close (idempotent; sets `status=closed` + `closed_at`). |

## Billing

Billing endpoints scope by `(tenant_id, workspace_id)` from the
request headers; the legacy `actor` Query param is retired (Plan 4).
`actor` remains as a column on `canon_cost_events` for audit /
forensics, but it no longer partitions queries.

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/billing` | Aggregated cost report. Query: `group_by` (csv of `date`/`model`/`agent_name`), `since`, `until`. |
| `GET`  | `/api/v1/billing/events` | Per-call drill-down (correlation id + subject + latency). Query: `agent_name`, `since`, `until`, `limit` (1-500), `offset`. |
| `GET`  | `/api/v1/billing/summary` | Rolling-window snapshot (24h / 7d / 30d) with top model per window. |
| `GET`  | `/api/v1/billing/top` | Top-N consumers by cost. Query: `dimension` (`model` / `agent_name`), `since`, `until`, `limit` (1-100). |
| `GET`  | `/api/v1/billing/by-subject` | Cost attribution per `(subject_kind, subject_id)`. Query: `subject_kind`, `since`, `until`, `limit` (1-200). |
| `GET`  | `/api/v1/billing/latency` | p50 / p95 / p99 latency per group. Query: `group_by` (csv of `model` / `agent_name`), `since`, `until`. |

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

## Agent surface

Curated subset of the user surface, protected by `X-Agent-Token`.
Tokens are tenant-scoped, minted via `POST /api/v1/agent-tokens`
(user tier, JWT-protected), and verified per-request against the
`canon_agent_tokens` table.

### Token management

These three routes are mounted under `/api/v1/agent-tokens` and are
themselves user-tier (operator JWT or anonymous in dev). Agent-tier
callers (whose actor begins with `agent:`) are refused with
`403 agent_cannot_mint`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/agent-tokens` | Mint. Returns 201 + [`AgentTokenCreated`](payload-reference.md#agenttokencreated). The **raw secret** is returned in the `token` field exactly ONCE. Capture it on the response; there is no recovery path. Subsequent reads expose only the public 12-char `prefix`. Body: [`AgentTokenMintRequest`](payload-reference.md#agenttokenmintrequest). |
| `GET`  | `/api/v1/agent-tokens` | List tokens for the current tenant (newest first). Returns `AgentTokenSummaryDto[]` -- the raw `token` is never round-tripped on this endpoint, only the `prefix` + metadata. |
| `DELETE` | `/api/v1/agent-tokens/{token_id}` | Revoke. Idempotent: revoking an already-revoked token is a no-op (still 204). Unknown `token_id` returns `404 resource_not_found`. |

### Agent endpoints

Every agent route requires three headers together: `X-Tenant-Id`,
`X-Workspace-Id`, and `X-Agent-Token: <secret>` (the raw token as
returned by the mint endpoint, shape: `agt_<8hex>_<32hex>`). The
`X-Agent-Token` header is mutually exclusive with `Authorization` --
presenting both is rejected at the conventions layer.

`Idempotency-Key` is **mandatory** on every agent-tier POST.
Missing -> `400 missing_idempotency_key`. (The user-tier POSTs
accept the header optionally; the agent surface is stricter so
duplicate machine calls can never silently create extra sources,
candidates, or queries.)

| Method | Path | Scope | Description |
|--------|------|-------|-------------|
| `POST` | `/api/v1/agent/sources` | `agent.sources:ingest` | Ingest a Source (sync default; `?mode=async` allowed). Body: `SubmitSourceJsonPayload`. **`Idempotency-Key` mandatory.** Reuses the same handler as the user-tier `POST /api/v1/sources`. |
| `GET`  | `/api/v1/agent/sources/{id}` | `agent.sources:read` | Retrieve a Source row. 404 -> `resource_not_found`. |
| `POST` | `/api/v1/agent/query` | `agent.query:run` | RAG answer with citations. Body: `AnswerRequest`. **`Idempotency-Key` mandatory.** Reuses the user-tier `POST /api/v1/query` handler. |
| `POST` | `/api/v1/agent/query/stream` | `agent.query:run` | Streaming RAG answer (Server-Sent Events). Body: `AnswerRequest`. **`Idempotency-Key` mandatory.** Same wire format as the user-tier `POST /api/v1/query/stream`. |
| `POST` | `/api/v1/agent/search` | `agent.query:run` | Hybrid retrieval (BM25 + vector + RRF), no LLM. Body: `SearchRequest`. **`Idempotency-Key` mandatory.** Reuses the user-tier `POST /api/v1/search` handler. |
| `GET`  | `/api/v1/agent/knowledge/{id}` | `agent.knowledge:read` | Fetch a KnowledgeItem (pointer view). 404 -> `knowledge_item_not_found`. |
| `GET`  | `/api/v1/agent/knowledge/{id}/provenance` | `agent.knowledge:read` | Fetch citation provenance for the current version. Query: `version` (optional explicit). |
| `POST` | `/api/v1/agent/candidates:propose` | `agent.candidates:propose` | Propose Candidate rows from a source. Body: `ProposeCandidateRequest`. **`Idempotency-Key` mandatory.** Does **not** auto-accept -- candidates land in `proposed` and must be reviewed via the user-tier `POST /api/v1/candidates/{id}:accept` route. |

### Scope strings

The per-route `scope` argument the verifier matches against the
token's `scopes` list:

| Scope | Endpoints it unlocks |
|-------|----------------------|
| `agent.sources:ingest` | `POST /api/v1/agent/sources` |
| `agent.sources:read` | `GET /api/v1/agent/sources/{id}` |
| `agent.query:run` | `POST /api/v1/agent/query`, `/api/v1/agent/query/stream`, `/api/v1/agent/search` |
| `agent.knowledge:read` | `GET /api/v1/agent/knowledge/{id}`, `/api/v1/agent/knowledge/{id}/provenance` |
| `agent.candidates:propose` | `POST /api/v1/agent/candidates:propose` |
| `*` | Wildcard -- matches every scope above. |

A token with `scopes: ["*"]` (the mint default) can call every agent
route; a token minted with a narrower list is restricted to those
routes exactly. Cross-scope calls return `403 agent_scope_denied`.

### Agent-tier error codes

In addition to the standard error envelope, the agent surface
raises:

| Status | Code slug | Raised when |
|--------|-----------|-------------|
| 401 | `missing_agent_token` | `X-Agent-Token` header is absent on an `/api/v1/agent/*` route. |
| 403 | `invalid_agent_token` | Token shape is malformed, prefix is unknown, tenant doesn't match, or the hash comparison fails. |
| 403 | `agent_token_expired` | The token's `expires_at` is in the past. |
| 403 | `agent_workspace_not_in_allowlist` | `X-Workspace-Id` is not in the token's `workspace_allowlist`. |
| 403 | `agent_scope_denied` | The per-route scope is not in the token's `scopes` list (and `*` is absent). |
| 403 | `agent_cannot_mint` | An agent-tier caller (actor begins with `agent:`) hit a user-tier `/api/v1/agent-tokens` route. |
| 400 | `missing_idempotency_key` | An agent POST was made without the mandatory `Idempotency-Key` header. |

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
`application/problem+json` document produced by
`flycanon.web.conventions.ProblemDetail` (the Plan 4 envelope; the
legacy `flycanon.interfaces.dtos.error.ProblemDetails` plural is
deleted). The shape:

```json
{
  "type": "https://firefly.dev/problems/knowledge_item_already_retired",
  "code": "knowledge_item_already_retired",
  "title": "Knowledge item already retired",
  "status": 409,
  "detail": "knowledge item 'add55fda-…' is already retired",
  "instance": "/api/v1/knowledge/add55fda-.../:retire",
  "correlation_id": "01HV...",
  "errors": []
}
```

Programmatic clients dispatch on `code` (stable snake_case slug);
`title` is now the human-readable label and `detail` is the
free-form message. The `type` URI base is
`https://firefly.dev/problems/...` (was `https://flycanon.dev/...`).
Field-level validation details land under `errors[]`.

### Status-code catalogue

| Status | Code slug | When |
|--------|-----------|------|
| 400 | `missing_tenant_context` | `X-Tenant-Id` / `X-Workspace-Id` header missing or malformed. |
| 400 | `invalid_request` | Pydantic validation, missing-field `ValueError` raised inside a controller helper. |
| 404 | `resource_not_found` | Generic missing resource (knowledge item, source, conversation, candidate, job, workspace). |
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
