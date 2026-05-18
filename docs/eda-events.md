<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **EDA events**

</div>

---

Three topics, all routed through `pyfly.eda.EventPublisher` and
backed by the durable Postgres outbox by default
(`FLYCANON_EDA_ADAPTER=postgres`). Flip to `memory` / `redis` /
`kafka` to swap brokers.

## flycanon.ingest

| Event type | Payload |
|------------|---------|
| `SourceIngested`         | `source_id`, `kind`, `content_sha256`, `n_chunks` |
| `SourceReplaced`         | `source_id`, `kind`, `content_sha256`, `n_chunks` (emitted by `PUT /api/v1/sources/{id}`) |
| `SourceIngestionFailed`  | `source_id`, `kind`, `code`, `message` |
| `IngestRequested`        | `job_id`, `payload` (consumed by the async-ingest worker -- see [async-ingest.md](async-ingest.md)) |

## flycanon.knowledge

| Event type | Payload |
|------------|---------|
| `KnowledgeItemPublished`  | `item_id`, `version`, `title`, `domain`, `status` |
| `KnowledgeItemDrafted`    | `item_id`, `version`, `status` |
| `KnowledgeItemSuperseded` | `item_id`, `version`, `superseded_by_item_id` |
| `KnowledgeItemRetired`    | `item_id`, `version`, `reason` |
| `KnowledgeRelationAdded`  | `relation_id`, `from_item_id`, `to_item_id`, `kind` |
| `KnowledgeRelationRemoved`| `relation_id`, `from_item_id`, `to_item_id`, `kind` |
| `CandidateProposed`       | `candidate_id`, `source_id`, `domain`, `score` |
| `CandidateAccepted`       | `candidate_id`, `materialised_knowledge_item_id`, `materialised_version` |
| `CandidateRejected`       | `candidate_id`, `reason` |

## flycanon.audit

A mirror of every mutation. Each event carries the full
`AuditEventRecorded` payload so downstream compliance projections can
rebuild the trail without re-querying flycanon.

| Field | Description |
|-------|-------------|
| `id`               | uuid of the audit row |
| `event_type`       | the same value pyfly publishes on the lifecycle topics, normalised (`source.ingested`, `knowledge.published`, `candidate.accepted`, ...) |
| `subject_kind`     | `source` / `knowledge_item` / `candidate` / `taxonomy` |
| `subject_id`       | id of the touched entity |
| `actor`            | optional caller identity |
| `correlation_id`   | W3C correlation id from the originating request |
| `occurred_at`      | server timestamp (ISO-8601, UTC) |
| `payload`          | event-specific dict |

## Consumer guarantees

* **At-least-once delivery.** Downstream consumers must be idempotent
  on `(event_type, subject_id, occurred_at)`.
* **Ordered per subject.** The Postgres outbox preserves insertion
  order; the Redis / Kafka adapters preserve ordering within their
  partitions but not across them.
* **Best-effort publish.** Publish failures are logged but never
  abort the originating mutation -- the durable record lives in
  Postgres (`canon_audit_events`).
