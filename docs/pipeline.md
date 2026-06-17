<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Pipeline**

</div>

---

# Pipeline

```
                          +-------------------------------+
   POST /api/v1/sources ->| SubmitSourceHandler           |
                          |  └-> IntakeService.submit     |
                          |       +- BinaryNormalizer    -+
                          |       |  magic-byte sniff     |
                          |       |  + Office / archive / |
                          |       |  email / image route  |
                          |       +-----------------------+
                          |       +- IngestionService    -+
                          |       |  loader + chunker     |
                          |       +-----------------------+
                          |       +- EmbeddingService    -+
                          |       |  batch embed          |
                          |       +-----------------------+
                          |       +- IndexService        -+
                          |       |  BM25 (Postgres tsv + |
                          |       |  GIN, default) +      |
                          |       |  vector store         |
                          |       |  (pgvector default)   |
                          |       +-----------------------+
                          |       +- AuditService        -+
                          |       |  source.ingested      |
                          |       +-----------------------+
                          |       +- EventPublisher      -+
                          |       |  flycanon.ingest      |
                          |       +-----------------------+
                          +-> SourceRecord
```

The intake stage accepts **any file format**. The binary normaliser
detects the media type from the magic bytes and routes the payload
through the matrix documented in
[`architecture.md`](architecture.md#universal-binary-normaliser)
(Office to Markdown, archives expanded, images OCR'd, emails
decomposed). Multi-artefact intakes are merged with `## Artifact:`
section markers so each chunk remains attributable.

### PDF ingestion -- two kinds, one pipeline

PDF is treated as a first-class format and supports **both** flavours
without any caller flag:

| PDF kind | What it is | How flycanon reads it |
|----------|------------|------------------------|
| **Full Digital Text PDF** | Born-digital -- Word / LibreOffice / LaTeX exports, browser "Save as PDF", reporting-pipeline output. Text is encoded as glyphs in the page stream. | Phase 1: PyMuPDF (`pymupdf`/`fitz`) `get_text()` returns the encoded text stream per page in microseconds without rendering. |
| **PDF-Image (scanned)** | Pages are raster images of the original -- scanned contracts, fax output, photos of receipts, mobile-camera captures. No encoded text on the page. | Phase 2: pages whose extracted text is shorter than `_MIN_CHARS_PER_PAGE` (16 chars) are rasterised by PyMuPDF at `_OCR_DPI` (200 DPI) and OCR'd via Tesseract (`pytesseract.image_to_string`). |
| **Hybrid PDF** | Some pages digital, some scanned (common for signed contracts: typed body + scanned signature page). | Phase 1 runs on every page; Phase 2 only fires for the pages flagged as image-only. The two phases compose page-by-page. |

The OCR engine is selectable: `FLYCANON_PDF_OCR_ENGINE=tesseract`
(default) or `FLYCANON_PDF_OCR_ENGINE=docling` after installing the
`docling` extra for layout-aware OCR with native multi-column /
table handling. OCR languages default to `eng+spa` and are
overridable via `FLYCANON_OCR_LANG`.

Encrypted PDFs are rejected up-front by `PdfGuard` (lightweight
`pypdf` pre-flight) with `error_code=encrypted_pdf`. Corrupt PDFs
fail fast with `error_code=corrupt_source`. PDFs are handled by the
PyMuPDF text-layer / Tesseract OCR path -- the plain UTF-8 `TextLoader`
is reserved as the last-resort fallback for unrecognised formats only.

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
          → HybridRetriever (core/services/retrieval/fusion.py):
                BM25 over Postgres tsvector + GIN on canon_chunks
                + dense over pgvector (same Postgres),
                fused via Reciprocal Rank Fusion (k = FLYCANON_RETRIEVAL_RRF_K)
          → hydrate hits with Postgres rows; apply caller filters
      → SearchResponse { hits, elapsed_ms }

POST /api/v1/query     (grounded answer with citations)
  → AnswerKnowledgeHandler → AnswerDispatcher.answer
      → FLYCANON_ANSWER_MODE selects the engine:

      rlm (default) → RLMAnswerService.answer
          → CanonCorpusBuilder: list in-scope sources, fetch each
                original from the ObjectStore, extract page text
                (sources without a stored original are skipped)
          → run the Recursive Language Model engine (RLMSession +
                AnthropicClient) in asyncio.to_thread: a CodeAct REPL
                that reasons over whole documents, not chunks
          → map engine citations back to Hit rows
          → AnswerResponse { answer, citations, model, elapsed_ms, no_answer }

      rag (deprecated, opt-in) → AnswerService.answer
          → log a deprecation warning (removal slated for a future release)
          → RetrievalService.search    (same path as /search)
          → render answer.yaml prompt + call FireflyAgent (output_type=AnswerOutput)
          → on primary-model error, fall back to FLYCANON_ANSWER_FALLBACK_MODEL
          → hydrate citations from the retrieved Hit rows
          → AnswerResponse { answer, citations, model, elapsed_ms, no_answer }
```

The RLM (default) path requires the original document bytes in the
object store -- keep `FLYCANON_STORE_ORIGINALS=true` -- and an
`ANTHROPIC_API_KEY` at runtime (the engine calls the Anthropic Messages
API directly). See [deployment.md](deployment.md#answer-mode-rlm-default--rag-deprecated).

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
