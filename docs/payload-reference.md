<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Payload reference**

</div>

---

All requests and responses are JSON. The wire shape is the source of
truth -- the OpenAPI doc at `/openapi.json` is generated from the
Pydantic models in
[`flycanon.interfaces.dtos`](../src/flycanon/interfaces/dtos/).

## ProblemDetails (RFC 7807)

Every non-2xx response is a problem document:

```json
{
  "type": "https://flycanon.dev/problems/knowledge-item-not-found",
  "title": "Knowledge item not found",
  "status": 404,
  "code": "knowledge_item_not_found",
  "detail": "knowledge item 'abc' not found",
  "extensions": { "item_id": "abc" }
}
```

`code` is the stable identifier SDKs branch on. The full table of
codes (and the HTTP status they map to) lives in the exception
advice
[`web/advice/exception_advice.py`](../src/flycanon/web/advice/exception_advice.py).

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

### `GET /api/v1/jobs/{id}`

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

### `GET /api/v1/jobs/{id}/stream`

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

Query: `group_by` (csv), `actor`, `since`, `until`.

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
