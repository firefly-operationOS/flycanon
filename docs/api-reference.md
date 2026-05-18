# API reference

The OpenAPI document is the canonical source -- visit `/openapi.json`
or the Swagger UI at `/docs` on a running instance. This page is the
human-readable catalogue.

## Sources

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sources` | Submit a source (JSON body with base64 bytes). Returns 201 + SourceRecord. |
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
| `GET`  | `/api/v1/knowledge/{id}/provenance` | Citation graph for the current version. Query: `version` (optional explicit). |

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
| `POST` | `/api/v1/search` | Hybrid retrieval (BM25 + vector + RRF). Body: `SearchRequest`. |
| `POST` | `/api/v1/query`  | RAG answer with citations. Body: `AnswerRequest`. |

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
