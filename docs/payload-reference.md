<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Payload reference**

</div>

---

All requests and responses are JSON. The wire shape is the source of
truth -- the OpenAPI doc at `/openapi.json` is generated from the
Pydantic models in
[`flycanon.interfaces.dtos`](../src/flycanon/interfaces/dtos/).

## ProblemDetail (RFC 7807)

Every non-2xx response is a problem document produced by
`flycanon.web.conventions.ProblemDetail` (Plan 4 envelope; replaces
the legacy plural `ProblemDetails`):

```json
{
  "type": "https://firefly.dev/problems/knowledge_item_not_found",
  "code": "knowledge_item_not_found",
  "title": "Knowledge item not found",
  "status": 404,
  "detail": "knowledge item 'abc' not found",
  "instance": "/api/v1/knowledge/abc",
  "correlation_id": "01HV...",
  "errors": []
}
```

`code` is the stable identifier SDKs branch on; `title` is the
human-readable label and `detail` is the free-form message. The
`type` URI base is `https://firefly.dev/problems/...`. The full
table of codes (and the HTTP status they map to) lives in
[`web/conventions/exceptions.py`](../src/flycanon/web/conventions/exceptions.py).

## Source intake

### Request -- `POST /api/v1/sources`

```json
{
  "kind": "docx",
  "uri": null,
  "metadata": {
    "title": "CANON Operational Knowledge",
    "author": "Elena Boffa Tarlatta",
    "domain": "process",
    "jurisdiction": "ES",
    "language": "es",
    "tags": ["mvp", "workshop"]
  },
  "content_base64": "...base64-encoded bytes...",
  "filename": "CANON-Operational-Knowledge.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

### Response -- 201 `SourceRecord`

```json
{
  "id": "0b06a8e6-37e7-4f7b-a8f1-7e2a6f7e5d11",
  "kind": "docx",
  "status": "ingested",
  "filename": "CANON-Operational-Knowledge.docx",
  "uri": null,
  "content_sha256": "8b3c0d...",
  "content_bytes": 915450,
  "n_chunks": 84,
  "metadata": { "title": "CANON Operational Knowledge", "domain": "process" },
  "created_at": "2026-05-18T17:00:00Z",
  "ingested_at": "2026-05-18T17:00:02Z",
  "updated_at": "2026-05-18T17:00:02Z"
}
```

## Knowledge lifecycle

### `CreateKnowledgeRequest`

```json
{
  "title": "Scope: in scope vs out of scope",
  "body": "## In scope ...",
  "summary": "Canonical scope statement for the MVP.",
  "domain": "process",
  "jurisdiction": "GLOBAL",
  "tags": ["scope", "mvp"],
  "citations": [
    {
      "source_id": "0b06a8e6-...",
      "chunk_id": "ce3...",
      "quote": "Scope > In scope ...",
      "relevance": 0.93
    }
  ],
  "publish": true,
  "actor": "andres.contreras@example.com"
}
```

### `UpdateKnowledgeRequest`

Identical shape minus `title` -- every field is optional. The handler
appends a new version (N+1) and flips the previous version to
`superseded`. Set `publish=false` to land the new version in `draft`.

### `SupersedeKnowledgeRequest`

```json
{
  "superseded_by_item_id": "another-item-id",
  "reason": "Replaced by the consolidated 2026 procedure",
  "actor": "process.owner@example.com"
}
```

### `RetireKnowledgeRequest`

```json
{
  "reason": "Procedure retired after the May 2026 reorganisation",
  "actor": "compliance.officer@example.com"
}
```

### `Provenance` (GET response)

```json
{
  "knowledge_item_id": "ki-...",
  "version": 2,
  "citations": [
    { "source_id": "src-...", "chunk_id": "ch-...", "quote": "...", "relevance": 0.91 }
  ],
  "sources": [
    { "id": "src-...", "kind": "docx", "title": "CANON Operational Knowledge", "content_sha256": "...", "n_chunks": 84 }
  ],
  "history": [
    { "knowledge_item_id": "ki-...", "version": 1, "status": "superseded", "title": "...", "body": "...", "created_at": "..." },
    { "knowledge_item_id": "ki-...", "version": 2, "status": "published",   "title": "...", "body": "...", "created_at": "..." }
  ]
}
```

## Candidates

### `ProposeCandidateRequest`

```json
{
  "source_id": "src-...",
  "domain": "process",
  "jurisdiction": "ES",
  "max_chunks": 40,
  "instructions": "Focus on F1..F4; ignore SOTA monitoring.",
  "actor": "andres.contreras@example.com"
}
```

### `CandidateRecord`

```json
{
  "id": "cand-...",
  "status": "proposed",
  "source_id": "src-...",
  "title": "Validation SLA per domain",
  "summary": "...",
  "body": "...",
  "domain": "process",
  "jurisdiction": "ES",
  "tags": ["sla"],
  "citations": [ { "source_id": "src-...", "chunk_id": "ch-...", "quote": "...", "relevance": 0.82 } ],
  "score": 0.86,
  "rationale": "Supported by chunks ch-1 and ch-2 ...",
  "materialised_knowledge_item_id": null,
  "materialised_version": null,
  "actor": "andres.contreras@example.com",
  "created_at": "2026-05-18T17:10:00Z",
  "decided_at": null,
  "metadata": {}
}
```

## Query

### `SearchRequest` / `SearchResponse`

```json
{
  "query": "What does the document say about scope?",
  "top_k": 8,
  "source_ids": ["src-..."],
  "domains": ["process"]
}
```

```json
{
  "hits": [
    {
      "chunk_id": "ch-...",
      "source_id": "src-...",
      "source_filename": "Cleargate Business Idea.docx",
      "source_title": "Cleargate Business Idea",
      "source_kind": "docx",
      "source_uri": null,
      "section_path": "9) Repositorio + Controlador WebFlux",
      "page": null,
      "knowledge_item_id": null,
      "knowledge_version": null,
      "content": "package com.cleargate.http; ...",
      "score": 0.62,
      "bm25_rank": null,
      "vector_rank": null,
      "metadata": {}
    }
  ],
  "elapsed_ms": 142
}
```

Every hit carries the rich source-side context (``source_filename``,
``source_title``, ``source_kind``, ``source_uri``, ``section_path``,
``page``) at the top level so a UI can render citation labels
directly. ``metadata`` keeps the forward-compatible bag for fields
the loader emits beyond the structured set above.

### `AnswerRequest` / `AnswerResponse`

```json
{
  "question": "Summarise the scope section in three sentences.",
  "top_k": 8,
  "instructions": null,
  "model": null
}
```

```json
{
  "answer": "The Cleargate policy engine is a deterministic evaluator built on Spring Boot 3 + WebFlux ...",
  "citations": [
    {
      "chunk_id": "ch-...",
      "source_id": "src-...",
      "source_filename": "Cleargate Business Idea.docx",
      "source_title": "Cleargate Business Idea",
      "source_kind": "docx",
      "section_path": "9) Repositorio + Controlador WebFlux",
      "page": null,
      "content": "package com.cleargate.http; ...",
      "score": 0.62,
      "metadata": {}
    }
  ],
  "model": "anthropic:claude-sonnet-4-6",
  "elapsed_ms": 842,
  "no_answer": false
}
```

The citation list reuses the same enriched ``Hit`` shape returned by
``/search`` -- ``source_filename`` and ``source_title`` give the
caller a direct human-readable label, and ``section_path`` / ``page``
locate the exact span the answer drew from. Grounded "I don't know"
responses look like ``{"answer": "", "citations": []}`` with
``no_answer: true``.

## Knowledge graph

### `GET /api/v1/knowledge/{id}/relations`

```json
{
  "outgoing": [
    { "id": "rel-...", "from_item_id": "...", "to_item_id": "...", "kind": "depends_on", "note": null, "created_at": "2026-05-18T12:00:00Z" }
  ],
  "incoming": []
}
```

### `POST /api/v1/knowledge/{id}/relations`

```json
{ "to_item_id": "...", "kind": "depends_on", "note": "..." }
```

`kind` is one of `related` / `depends_on` / `conflicts_with` /
`replaces`. The (from, to, kind) tuple is unique -- duplicates return
`409 relation_already_exists`.

### `GET /api/v1/knowledge:graph`

JSON view (default):

```json
{
  "nodes": [
    { "id": "ki-...", "label": "Data retention", "domain": "compliance", "kind": "knowledge_item" }
  ],
  "edges": [
    { "from": "ki-1", "to": "ki-2", "kind": "conflicts_with" }
  ]
}
```

Mermaid view (`Accept: text/vnd.mermaid`):

```
graph LR
    ki1["Data retention"] -->|conflicts_with| ki2["Data retention -- security"]
```

Query: `domain`, `kind`, `include_sources=true|false`.

### `GET /api/v1/knowledge/{id}/diff`

```json
{
  "from_version": 1,
  "to_version": 2,
  "unified_diff": "@@ -1,3 +1,3 @@\n- old line\n+ new line\n ...",
  "field_changes": [
    { "field": "title", "from": "Data retention", "to": "Data retention v2" }
  ],
  "added_citations": ["src-..."],
  "removed_citations": []
}
```

## Conversations

### `POST /api/v1/conversations/{id}/turns`

```json
{ "query": "What about the finance domain?", "max_chunks": 8 }
```

```json
{
  "turn_id": "trn-...",
  "answer": "...",
  "citations": [ /* same Hit shape as /query */ ],
  "model": "anthropic:claude-sonnet-4-6"
}
```

### `POST /api/v1/conversations/{id}/suggest`

```json
{ "questions": ["What changed in v2?", "Who approved this?", "..."] }
```

## Async ingest jobs

### `POST /api/v1/sources:async`

Same body as `POST /api/v1/sources`. Response:

```json
{ "job_id": "job-...", "status": "queued" }
```

### `GET /api/v1/ingest-jobs/{id}`

```json
{
  "id": "job-...",
  "status": "running",
  "progress": 0.42,
  "stage": "embedding",
  "source_id": null,
  "error_code": null,
  "error_message": null,
  "created_at": "2026-05-18T12:00:00Z",
  "updated_at": "2026-05-18T12:00:18Z"
}
```

### `GET /api/v1/ingest-jobs/{id}/stream`

Server-Sent Events. See [async-ingest.md](async-ingest.md) for the
frame format. Reconnect with `?cursor=N` to resume.

## Quality scans

### `GET /api/v1/knowledge:stale` -- `StaleReport`

```json
{
  "items": [
    { "knowledge_item_id": "...", "title": "...", "domain": "...", "score": 0.42, "max_similarity": 0.58, "sample_size": 12, "computed_at": "2026-05-18T12:00:00Z" }
  ],
  "total": 1
}
```

### `POST /api/v1/knowledge:detect-conflicts` -- `ConflictScanResponse`

```json
{ "domain": "compliance", "min_similarity": 0.85, "max_items": 50, "actor": "u-1" }
```

```json
{
  "pairs_evaluated": 36,
  "conflicts_found": 4,
  "candidate_ids": ["cand-...", "..."],
  "relation_ids":  ["rel-...",  "..."]
}
```

## Billing

### `GET /api/v1/billing`

Query: `group_by` (csv), `since`, `until`. Scope is supplied by the
`X-Tenant-Id` + `X-Workspace-Id` headers (Plan 4); the legacy
`actor` Query param is retired.

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

### `GET /api/v1/billing/events` -- `CostEventsPage`

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

### `GET /api/v1/billing/summary` -- `BillingSummary`

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
    "top_actor": "u-1",
    "top_actor_cost_usd": "1.95"
  },
  "last_7d":  { /* same shape as last_24h */ },
  "last_30d": { /* same shape as last_24h */ }
}
```

### `GET /api/v1/billing/top` -- `TopConsumersReport`

```json
{
  "dimension": "model",
  "rows": [
    {
      "dimension": "model",
      "value": "anthropic:claude-sonnet-4-6",
      "input_tokens": 312000,
      "output_tokens": 78000,
      "total_tokens": 390000,
      "cost_usd": "3.82",
      "calls": 120
    }
  ]
}
```

Invalid `dimension` -> RFC 7807 `bad_request_exception`.

### `GET /api/v1/billing/by-subject` -- `SubjectCostReport`

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

### `GET /api/v1/billing/latency` -- `LatencyReport`

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

## Corpus inventory

### `GET /api/v1/stats` -- `CorpusStats`

```json
{
  "generated_at": "2026-05-18T17:00:00Z",
  "sources": {
    "total": 142,
    "by_kind":   { "pdf": 87, "docx": 36, "html": 12 },
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

## PII

When `FLYCANON_PII_POLICY=reject`, the intake controller returns:

```json
{
  "type": "about:blank",
  "title": "PII detected",
  "status": 422,
  "code": "pii_violation",
  "detail": "ingest rejected: 2 personal-data finding(s) (kinds: email, ssn)",
  "findings": [
    { "kind": "email", "start": 1234, "end": 1257 },
    { "kind": "ssn",   "start": 4500, "end": 4511 }
  ]
}
```

See [pii.md](pii.md) for the full policy matrix.

## Agent tokens

The three DTOs below back the `/api/v1/agent-tokens` user-tier
CRUD. Source: `src/flycanon/interfaces/dtos/agent_token.py`.

> **Security note.** The full secret `token` field appears on the
> wire in `AgentTokenCreated` **exactly once** -- the response of
> `POST /api/v1/agent-tokens`. Subsequent reads (`GET /api/v1/agent-tokens`,
> per-id lookups, audit trails) only ever expose the 12-char public
> `prefix`. The server persists the SHA-256 hash of the full token,
> not the token itself, so there is no recovery path: capture the
> secret at mint time or revoke + remint.

### `AgentTokenMintRequest`

Request body for `POST /api/v1/agent-tokens`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string (1-128 chars) | yes | Human-readable label, surfaced in the list view. |
| `workspace_allowlist` | `string[]` \| null | no | When set, restricts the token to those workspace ids under the tenant. `null` (the default) means any workspace. |
| `scopes` | `string[]` | no | Per-route scopes the token can satisfy. Defaults to `["*"]` (wildcard). See [api-reference.md -> Scope strings](api-reference.md#scope-strings). |
| `rate_limit_rpm` | int (1-10 000) \| null | no | Advisory metadata. Persisted but **not yet enforced** by the verify path -- reserved for the per-token rate limiter we add later. |
| `expires_at` | datetime \| null | no | When set, the verify path raises `agent_token_expired` once the wall clock passes it. |

```jsonc
{
  "name": "ci-runner",
  "workspace_allowlist": ["ws-prod", "ws-staging"],
  "scopes": ["agent.sources:ingest", "agent.query:run"],
  "rate_limit_rpm": 60,
  "expires_at": "2026-12-31T23:59:59Z"
}
```

### `AgentTokenSummaryDto`

Response shape returned from `GET /api/v1/agent-tokens` (one per
row) and used as the base for `AgentTokenCreated`. The secret is
deliberately omitted; only the public 12-char `prefix` is exposed.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Internal token id (hex). |
| `name` | string | Display name from mint. |
| `prefix` | string | Public 12-char prefix (`agt_<8hex>`). Lookup key for the verify path. |
| `workspace_allowlist` | `string[]` \| null | Echo of the mint payload. `null` = any workspace under the tenant. |
| `scopes` | `string[]` | Echo of the mint payload. `["*"]` matches every per-route scope. |
| `rate_limit_rpm` | int \| null | Echo of the mint payload. Advisory; not enforced today. |
| `expires_at` | datetime \| null | Echo of the mint payload. Verified at use time. |
| `created_at` | datetime | When the token was minted. |
| `created_by` | string | Actor that minted the token (operator JWT subject; `"anonymous"` in unauthenticated dev contexts). |
| `revoked_at` | datetime \| null | Set by `DELETE /api/v1/agent-tokens/{id}`. Revoked tokens fail verify with `invalid_agent_token`. |
| `last_used_at` | datetime \| null | Touched by every successful verify -- useful for finding stale credentials. |

### `AgentTokenCreated`

Response shape for `POST /api/v1/agent-tokens` -- extends
`AgentTokenSummaryDto` with the raw secret:

| Field | Type | Description |
|-------|------|-------------|
| *(every field on `AgentTokenSummaryDto`)* | | |
| `token` | string | **Raw secret, returned ONCE.** Shape: `agt_<8hex>_<32hex>`. Persisted server-side only as a SHA-256 hash; never round-tripped through any other endpoint. Capture this on the response and store it the way you would any other long-lived credential. |

```jsonc
{
  "id": "9f4...",
  "name": "ci-runner",
  "prefix": "agt_a1b2c3d4",
  "workspace_allowlist": ["ws-prod"],
  "scopes": ["agent.sources:ingest"],
  "rate_limit_rpm": null,
  "expires_at": null,
  "created_at": "2026-05-22T18:00:00Z",
  "created_by": "user-42",
  "revoked_at": null,
  "last_used_at": null,
  "token": "agt_a1b2c3d4_e5f6...32hex"
}
```

## Workspace lifecycle events

The three DTOs below are the on-wire payloads for the
`canon.workspaces.v1` topic introduced in Plan 6. Source:
`src/flycanon/interfaces/dtos/workspace_event.py`. Consumers dispatch
on `event_type`; the lifecycle mapping to flycanon routes lives in
[architecture.md -> Workspace lifecycle events](architecture.md#workspace-lifecycle-events).

All three events share the base fields:

| Field | Type | Description |
|-------|------|-------------|
| `tenant_id` | string (1-64 chars) | Canonical tenant slug. |
| `workspace_id` | string (1-64 chars) | Canonical workspace slug. |
| `occurred_at` | datetime (timezone-aware UTC) | Publisher clock at emit time. |
| `event_type` | string literal | Dispatch key: `workspace.created` / `workspace.updated` / `workspace.deleted`. |

### `WorkspaceCreated`

Emitted from `POST /api/v1/workspaces`. The payload mirrors the on-wire
`WorkspaceSpec` so a consumer can rebuild its cache row directly.

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | `"workspace.created"` | Discriminator literal. |
| `name` | string (1-255 chars) | Workspace display name. |
| `scope` | `list[Any]` \| null | Scope bullets as stored in `scope_json`. |
| `sme_roster` | `list[Any]` \| null | SME roster as stored in `sme_roster_json`. |
| `retention_days` | int \| null | Audit-retention window for the workspace. |
| `jurisdiction` | string \| null | Workspace jurisdiction tag. |

```jsonc
{
  "event_type": "workspace.created",
  "tenant_id": "acme",
  "workspace_id": "ws-prod",
  "occurred_at": "2026-05-22T18:00:00Z",
  "name": "Production",
  "scope": ["compliance", "process"],
  "sme_roster": [{"role": "owner", "actor": "u-1"}],
  "retention_days": 365,
  "jurisdiction": "ES"
}
```

### `WorkspaceUpdated`

Emitted from `PATCH /api/v1/workspaces/{id}`. Carries the
**post-update** row state (not just the patched fields) so consumers
can replace their cached row wholesale -- same payload shape as
`WorkspaceCreated`.

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | `"workspace.updated"` | Discriminator literal. |
| `name` | string (1-255 chars) | Post-update workspace name. |
| `scope` | `list[Any]` \| null | Post-update scope. |
| `sme_roster` | `list[Any]` \| null | Post-update SME roster. |
| `retention_days` | int \| null | Post-update retention window. |
| `jurisdiction` | string \| null | Post-update jurisdiction. |

```jsonc
{
  "event_type": "workspace.updated",
  "tenant_id": "acme",
  "workspace_id": "ws-prod",
  "occurred_at": "2026-05-22T18:05:00Z",
  "name": "Production EU",
  "scope": ["compliance", "process", "security"],
  "sme_roster": [{"role": "owner", "actor": "u-1"}],
  "retention_days": 730,
  "jurisdiction": "EU"
}
```

### `WorkspaceDeleted`

Emitted from `POST /api/v1/workspaces/{id}:close`. flycanon has no
hard-delete route; closing a workspace is the terminal lifecycle
state, and the row is preserved (`status=closed`) for audit. The
event name matches the canonical lifecycle vocabulary so downstream
consumers don't have to special-case the soft-delete semantics. No
payload fields beyond the base -- the `(tenant_id, workspace_id)`
pair is enough for the consumer to drop or tombstone its cached row.

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | `"workspace.deleted"` | Discriminator literal. |

```jsonc
{
  "event_type": "workspace.deleted",
  "tenant_id": "acme",
  "workspace_id": "ws-prod",
  "occurred_at": "2026-05-22T19:00:00Z"
}
```
