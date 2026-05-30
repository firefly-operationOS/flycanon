<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Glossary**

</div>

---

| Term | Definition |
|------|------------|
| **Source** | A raw inbound artefact -- a DOCX, a PDF, an HTML page. flycanon stores its metadata + chunks, never the original bytes. |
| **Chunk** | A retrieval-grade fragment of a source. Carries a `section_path`, a 0-based `index_in_source`, and an optional dense embedding. |
| **Knowledge item** | The canonical pointer for a unit of operational knowledge. Carries the current version, status, domain, jurisdiction. |
| **Knowledge version** | A single revision of a knowledge item. Append-only -- updates produce a new version row; the previous one transitions to `superseded`. |
| **Candidate** | A pre-canonical knowledge proposal emitted by the consolidation stage. Lives in `proposed` until a human accepts or rejects it. |
| **Citation** | A pointer from a knowledge version to a source chunk. The (verbatim) quote + the relevance score travel with the edge. |
| **Provenance** | The resolved citation graph for one knowledge version, plus the source summaries it touches, plus the version chain for its item. |
| **Domain** | The operational area a knowledge item belongs to. Default tree: legal, compliance, process, network, ai_platform, executive, hr, cto, engineering, security. |
| **Jurisdiction** | The geographic / legal scope of a knowledge item (e.g. ES, EU, GLOBAL). |
| **Taxonomy node** | A node in the domain / jurisdiction tree. Default seed inserts one root per Domain. |
| **Hybrid retrieval** | BM25 over the Postgres `tsvector` + GIN corpus fused with dense-vector search over pgvector, combined via Reciprocal Rank Fusion. |
| **RAG answer** | An LLM-written answer grounded in the top retrieval hits, with the chunks the model actually relied on returned as `citations`. |
| **Supersession** | Marking one item or version as replaced by another. `:supersede` redirects at the item level; an `update` transitions only the previous version. |
| **Audit event** | An append-only row capturing one mutation: event_type, subject, actor, correlation_id, payload. Also broadcast on `flycanon.audit`. |
