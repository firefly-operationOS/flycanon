<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Knowledge quality**

</div>

---

Two on-demand scans keep the canon honest:

* **Staleness** -- flags items whose canonical body no longer matches
  recent sources for the same topic. Surfaces "this policy was right
  in 2024 but the regulations changed in 2026" before a user catches
  it.
* **Conflict detection** -- flags pairs of items that make
  *contradicting* canonical claims about the same topic. Surfaces
  "the legal team and the security team published different
  data-retention rules" before answers cite one and ignore the
  other.

Both are launched manually from the inbox / admin UI -- they're LLM-
or embedding-heavy, so v1 doesn't run them on every write.

## Staleness

```
GET /api/v1/knowledge:stale
```

For each published knowledge item:

1. Embed the current version body.
2. Cosine-similar against the embeddings of sources ingested in the
   trailing window (default 60 days).
3. `score = 1 - max(similarity)` -- high score == the canon disagrees
   with what fresh sources are saying.

The score is computed lazily and cached on
`KnowledgeItemRow.metadata_json.staleness` with a 6h TTL, so opening
the inbox doesn't re-burn the embedding bill on every page load.

Response shape (`StaleReport`):

```json
{
  "items": [
    {
      "knowledge_item_id": "...",
      "title": "Data retention -- finance",
      "domain": "finance",
      "score": 0.42,
      "max_similarity": 0.58,
      "sample_size": 12,
      "computed_at": "2026-05-18T12:34:56Z"
    }
  ],
  "total": 1
}
```

## Conflict detection

```
POST /api/v1/knowledge:detect-conflicts
{
  "domain": "compliance",         // optional narrow filter
  "min_similarity": 0.85,         // RRF candidate threshold
  "max_items": 50,                // bound the pairwise pass
  "actor": "u-123"
}
```

Pipeline:

1. **Cluster.** Embed every published item body (`O(N)`), pairwise
   cosine, keep pairs >= `min_similarity` as candidates.
2. **Judge.** The configured answer model receives each candidate
   pair and emits a structured `ConflictJudgment`
   (`is_conflict` + `confidence` + `reasoning`).
3. **Record.** Confirmed conflicts land as `CandidateRow`s with
   `metadata.kind=conflict_detection` -- the inbox UI queues them
   alongside the standard candidate stream.
4. **Link.** When wired with `KnowledgeRelationService`, the detector
   also materialises a `conflicts_with` edge in the knowledge graph
   so the graph view and provenance UI surface the link immediately,
   not only after a human accepts the candidate.

Response shape (`ConflictScanResponse`):

```json
{
  "pairs_evaluated": 36,
  "conflicts_found": 4,
  "candidate_ids": ["...", "...", "...", "..."],
  "relation_ids":  ["...", "...", "...", "..."]
}
```

`relation_ids` is empty when the detector was wired without a
relation-service binding (e.g. a unit-test harness).

## Why these are launched manually

The judge call is a small LLM round-trip per candidate pair and the
combinatorial explosion on a large canon is real
(`O(N^2)` pairs, gated by `min_similarity`). Running it on every
write would dominate the LLM bill; running it nightly from a cron is
the natural next step once thresholds are tuned.

The staleness scan is cheaper -- only embedding calls -- and could
run on a schedule, but v1 keeps both surfaces user-driven for budget
predictability.
