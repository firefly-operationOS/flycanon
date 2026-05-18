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
| `POST` | `/api/v1/sources` | Submit a source (JSON body with base64 bytes **or** `url` to fetch). Returns 201 + SourceRecord. |
| `POST` | `/api/v1/sources:bulk` | Bulk-submit an array of sources. Returns per-item `BulkSourceResult`s. |
| `POST` | `/api/v1/sources:async` | Enqueue an async ingest job; returns the job id. Stream progress on `/api/v1/jobs/{id}/stream`. |
| `PUT`  | `/api/v1/sources/{id}` | Replace an existing source's content in place. Body: `SubmitSourceRequest` (with new bytes / URL). |
| `GET`  | `/api/v1/sources` | Paginated list. Query: `status`, `kind` (csv), `limit`, `offset`. |
| `GET`  | `/api/v1/sources/{id}` | Fetch a single source. 404 -> `source_not_found`. |

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
| `DELETE` | `/api/v1/knowledge/relations/{relation_id}` | Remove an edge. |
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
| `POST` | `/api/v1/query:stream` | Same as `/query` but streams tokens as Server-Sent Events. |

## Conversations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/conversations` | Start a new conversation. Returns the conversation id. |
| `GET`  | `/api/v1/conversations/{id}` | Fetch conversation header + rolling summary + last N turns. |
| `POST` | `/api/v1/conversations/{id}/turns` | Submit a user turn; returns the assistant answer with citations. |
| `GET`  | `/api/v1/conversations/{id}/turns` | Paginated turn history. |
| `POST` | `/api/v1/conversations/{id}/suggest` | LLM-suggested follow-up questions based on the turn history. |

## Async ingest jobs

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/jobs/{id}` | Job header (status, progress, counters). |
| `GET`  | `/api/v1/jobs/{id}/stream` | Server-Sent Events feed of job events (cursor-based). |
| `POST` | `/api/v1/jobs/{id}:cancel` | Co-operative cancellation request. |

## Billing

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/billing` | Aggregated cost report. Query: `group_by` (csv of `date`/`model`/`agent_name`/`actor`), `actor`, `since`, `until`. |

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
