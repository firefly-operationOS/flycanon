# Pipeline

```
                          ┌────────────────────────────┐
   POST /api/v1/sources ─►│ SubmitSourceHandler        │
                          │  └─► IntakeService.submit  │
                          │       ┌─ IngestionService ─┐
                          │       │  loader + chunker  │
                          │       └────────────────────┘
                          │       ┌─ EmbeddingService ─┐
                          │       │  batch embed       │
                          │       └────────────────────┘
                          │       ┌─ IndexService      ┐
                          │       │  SqliteCorpus +    │
                          │       │  sqlite-vec        │
                          │       └────────────────────┘
                          │       ┌─ AuditService      ┐
                          │       │  source.ingested   │
                          │       └────────────────────┘
                          │       ┌─ EventPublisher    ┐
                          │       │  flycanon.ingest   │
                          │       └────────────────────┘
                          └─► SourceRecord
```

```
POST /api/v1/candidates:propose
  → ProposeCandidatesHandler → CandidateService.propose_from_source
      → Consolidator
          → FireflyAgent (provider:model, output_type=ConsolidationOutput)
          → drops citations whose chunk_id isn't in the supplied window
      → persist candidate rows in ``proposed`` status
      → AuditService.record  + EventPublisher.publish(CandidateProposed)

POST /api/v1/candidates/{id}:accept
  → AcceptCandidateHandler → CandidateService.accept
      → KnowledgeService.create()  (or .update() when target_item_id is set)
      → audit + publish(CandidateAccepted, KnowledgeItemPublished)

POST /api/v1/candidates/{id}:reject
  → RejectCandidateHandler → CandidateService.reject
      → flip candidate status to ``rejected``
      → audit + publish(CandidateRejected)
```

```
POST /api/v1/search    (raw hybrid retrieval)
  → SearchKnowledgeHandler → SearchService.search
      → RetrievalService.search
          → HybridRetriever (agentic):
                BM25 over SqliteCorpus + dense over sqlite-vec,
                fused via Reciprocal Rank Fusion (k = FLYCANON_RETRIEVAL_RRF_K)
          → hydrate hits with Postgres rows; apply caller filters
      → SearchResponse { hits, elapsed_ms }

POST /api/v1/query     (grounded answer with citations)
  → AnswerKnowledgeHandler → AnswerService.answer
      → RetrievalService.search    (same path as /search)
      → render answer.yaml prompt + call FireflyAgent (output_type=AnswerOutput)
      → on primary-model error, fall back to FLYCANON_ANSWER_FALLBACK_MODEL
      → hydrate citations from the retrieved Hit rows
      → AnswerResponse { answer, citations, model, elapsed_ms, no_answer }
```

```
GET /api/v1/knowledge/{id}/provenance
  → GetProvenanceHandler → ProvenanceService.resolve
      → list_citations(version) + summary of every source they touch
      → full version history for the item
      → Provenance { knowledge_item_id, version, citations, sources, history }
```

## Lifecycle events

| Event type                      | Topic              | Carried payload (subset) |
|---------------------------------|--------------------|--------------------------|
| `SourceIngested`                | `flycanon.ingest`  | source_id, kind, content_sha256, n_chunks |
| `SourceIngestionFailed`         | `flycanon.ingest`  | source_id, kind, code, message |
| `KnowledgeItemPublished`        | `flycanon.knowledge` | item_id, version, title, domain, status |
| `KnowledgeItemSuperseded`       | `flycanon.knowledge` | item_id, version, superseded_by_item_id |
| `KnowledgeItemRetired`          | `flycanon.knowledge` | item_id, version, reason |
| `CandidateProposed`             | `flycanon.knowledge` | candidate_id, source_id, domain, score |
| `CandidateAccepted`             | `flycanon.knowledge` | candidate_id, materialised_knowledge_item_id, materialised_version |
| `CandidateRejected`             | `flycanon.knowledge` | candidate_id, reason |
| `AuditEventRecorded`            | `flycanon.audit`   | id, event_type, subject_kind, subject_id, actor, correlation_id, payload |
