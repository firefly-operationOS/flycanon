# Payload reference

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
      "knowledge_item_id": null,
      "knowledge_version": null,
      "content": "Scope > In scope ...",
      "score": 0.62,
      "bm25_rank": null,
      "vector_rank": null,
      "metadata": { "section_path": "Scope > In scope" }
    }
  ],
  "elapsed_ms": 142
}
```

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
  "answer": "The scope covers the MVP feature set ...",
  "citations": [
    { "chunk_id": "ch-...", "source_id": "src-...", "content": "Scope > In scope ...", "score": 0.62, "metadata": {} }
  ],
  "model": "anthropic:claude-sonnet-4-6",
  "elapsed_ms": 842,
  "no_answer": false
}
```
