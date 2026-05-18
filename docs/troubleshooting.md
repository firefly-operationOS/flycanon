<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Troubleshooting**

</div>

---

A problem -> root cause -> fix index for common production failure
modes. Organised by **the symptom you'll actually see** rather than
by subsystem.

---

## Service boot

### `relation "canon_sources" does not exist`

The schema isn't on the database yet.

**Fix.** Run migrations once before booting `serve` / `worker`:

```bash
docker run --rm --env-file .env \
  ghcr.io/firefly-operationos/flycanon:latest migrate
```

In docker-compose, set `RUN_MIGRATIONS=true` (the default in
`env_template`) so the entrypoint does it on startup. In Kubernetes,
prefer a one-shot `migrate` Job that runs **before** the API rollout.

---

### `pgvector` extension not found / `type "vector" does not exist`

The Postgres instance is up but doesn't have `pgvector` installed.

**Fix.** Use the `pgvector/pgvector:pg16` image (the docker-compose
default), or `CREATE EXTENSION vector;` against your managed
Postgres if it's available there (RDS, Cloud SQL, Azure Postgres
Flexible Server all ship the extension).

```sql
\c flycanon
CREATE EXTENSION IF NOT EXISTS vector;
```

flycanon will not boot the `pgvector` backend without this -- pick a
different backend via `FLYCANON_VECTOR_STORE` (`sqlite-vec` for a
file-backed alternative) if your Postgres doesn't have `vector`.

---

### `ModuleNotFoundError: pyfly` / `fireflyframework_agentic`

The image was built without the BuildKit named contexts that pull
the sibling repos in.

**Fix.** Use the published image
(`ghcr.io/firefly-operationos/flycanon:latest`) -- the publish
workflow clones both siblings into `./vendor/` and passes them via
`--build-context`. If you're building locally, the dance is:

```bash
docker buildx build \
  --build-context pyfly=../../fireflyframework/fireflyframework-pyfly \
  --build-context fireflyframework-agentic=../../fireflyframework/fireflyframework-agentic \
  --tag flycanon:dev .
```

See [cicd.md § Build contexts](cicd.md#build-contexts).

---

### `/actuator/health/readiness` returns 503 with `eda_health` DOWN

The Postgres outbox (`pyfly_eda_outbox`) isn't reachable -- typically
because Postgres is still starting, or `LISTEN/NOTIFY` is blocked by
a pg_bouncer in `transaction` mode in front of it.

**Fix.**

* Wait 3-5s for Postgres to finish its first start (the readiness
  probe will recover automatically).
* If using pg_bouncer in `transaction` pooling mode, point flycanon
  at a `session`-mode pool or directly at Postgres. `LISTEN/NOTIFY`
  doesn't survive transaction-level pooling.
* Or switch to a different EDA adapter: `FLYCANON_EDA_ADAPTER=memory`
  (single-process, no durability), `redis` (Redis Streams), or
  `kafka`.

---

## Ingestion

### `POST /api/v1/sources` returns 422 with `code=encrypted_pdf`

`PdfGuard` rejected the upload because the PDF requires a password.

**Fix.** Decrypt the PDF before submission (`qpdf
--decrypt --password=<pwd> in.pdf out.pdf`). flycanon intentionally
does **not** accept passwords on the wire because the canonical
bytes are never stored, and storing the password to re-open a row
later would defeat the design.

---

### `POST /api/v1/sources` returns 422 with `code=corrupt_source` / `unsupported_binary`

The binary normaliser couldn't detect or decode the upload. Common
causes:

* The file is truncated / mid-download.
* The magic bytes don't match the extension (e.g. a `.docx` that's
  actually a renamed PDF).
* The format is genuinely outside flycanon's matrix (proprietary
  CAD, encrypted archive, ...).

**Fix.** Check the `error_message` on the `SourceRecord` for the
specific reason. Re-upload the corrected file. For exotic formats,
the universal MarkItDown loader is the last-resort fallback -- if
MarkItDown can't read it, neither can flycanon today.

---

### `POST /api/v1/sources` accepted but returns `n_chunks=0`

The loader read the document but emitted no text. Most common cause
for PDFs:

* **PDF is image-only** but Tesseract couldn't extract anything --
  pages are blank scans, watermark-only pages, or the language pack
  isn't installed for the document language. Check the worker logs
  for `PDF OCR fallback unavailable: ...` warnings.

**Fix.**

* Install the Tesseract language pack you need
  (`tesseract-ocr-<lang>`) in a derived image, or set
  `FLYCANON_OCR_LANG=eng+spa+fra+...` to compose the languages you
  ship.
* For scanned PDFs with complex layouts (multi-column, tables),
  switch to the Docling engine
  (`FLYCANON_PDF_OCR_ENGINE=docling`) -- it preserves reading order
  and table structure that Tesseract collapses.

See [pipeline.md § PDF ingestion](pipeline.md#pdf-ingestion----two-kinds-one-pipeline)
for the full two-phase strategy (text-layer + OCR fallback) and
when each fires.

---

### `POST /api/v1/sources` slow on large bundles

ZIP / 7Z / TAR archives are expanded recursively (capped at
`binary_max_recursion_depth` and `binary_max_expanded_files`). Each
child re-enters the normaliser. A 200-MB archive of scanned PDFs is
hundreds of OCR runs.

**Fix.**

* Submit archives **asynchronously** if your pipeline can wait --
  flycanon's `worker` role processes ingestion in the background
  when paired with the `/api/v1/sources?async=true` flag (see
  api-reference for the async submission path).
* Pre-extract the archive client-side and submit only the artefacts
  you actually need.
* Raise `FLYCANON_BINARY_MAX_EXPANDED_FILES` for legitimately large
  bundles, lower it as a safety valve in shared deployments.

---

## Embeddings + retrieval

### `psycopg.errors.DataException: expected N dimensions, not M`

The embedding model's output dimension doesn't match the `pgvector`
column dimension.

**Fix.** Either:

* Change `FLYCANON_EMBEDDING_MODEL` to a model whose dimension
  matches the existing column (`FLYCANON_EMBEDDING_DIMENSIONS` must
  also match -- it's used at boot to validate the schema).
* **Re-index**: drop the `canon_chunk_vectors` table, set
  `FLYCANON_EMBEDDING_DIMENSIONS` to the new model's dimension, run
  `migrate`, then re-ingest the corpus. This is **destructive** --
  back up first if you care about the audit trail.

There is no in-place migration between embedding models. Dimensions
are part of the index, and HNSW doesn't gracefully accept
mid-flight dimension changes.

---

### `Embedding provider returned 429 / rate limited`

The embedding provider is rate-limiting flycanon.

**Fix.**

* Set `FLYCANON_EMBEDDING_MAX_CONCURRENT_REQUESTS` to a lower number
  (defaults to 8). Long ingestions queue rather than blast.
* Use a different provider with higher limits, or self-host an
  Ollama model (`FLYCANON_EMBEDDING_MODEL=ollama:nomic-embed-text`)
  for offline embedding.
* For burst-y ingestion, set `FLYCANON_EMBEDDING_RETRY_ATTEMPTS=5`
  -- exponential backoff covers transient 429s without giving up.

---

### `/api/v1/search` returns 0 hits even though the source ingested

A few common causes:

| Cause | How to check | Fix |
|-------|--------------|-----|
| Embedding model rotated since ingest -- the vectors in `canon_chunk_vectors` were produced by a model that doesn't share an embedding space with the current query embedder. | Look at `canon_sources.metadata_json -> 'embedding_model'`. | Re-ingest the source under the current model. |
| BM25 `tsvector` config mismatch -- the chunks were indexed under one language config (`spanish`) but the query is being run under another (`english`). | `SHOW default_text_search_config;` against Postgres. | Pin both via `FLYCANON_BM25_TEXT_SEARCH_CONFIG`. |
| Filters too tight -- `domain` / `jurisdiction` / `status` filters compose with `AND`. | Drop the filters one by one. | Widen the filters or `per_query_k`. |
| The text-search config is `simple` (multilingual, no stemming) but the query needs stemming. | Try the query with quoted exact terms. | Switch to `english` / `spanish` / ... via `FLYCANON_BM25_TEXT_SEARCH_CONFIG`. |

---

### `/api/v1/query` returns `no_answer=true` even though the corpus has the answer

The retrieval window doesn't contain enough evidence. Two knobs:

* **`top_k`** -- raise to widen the evidence band (default 8;
  bumping to 16-24 is fine for most corpora).
* **`per_query_k`** -- the per-modality cap before RRF fusion.
  Raise this when filters drop many candidates (default 30).

When neither helps, the corpus genuinely lacks the answer. flycanon
**intentionally never confabulates** -- prompted to return
`no_answer=true` rather than guess. Add the relevant source and the
next query will succeed.

---

## Answer model

### `/api/v1/query` returns 502 with `code=answer_model_failed`

The configured `FLYCANON_ANSWER_MODEL` failed and there's no
fallback configured (or the fallback failed too).

**Fix.** Configure `FLYCANON_ANSWER_FALLBACK_MODEL` (e.g.
`openai:gpt-4o` when the primary is `anthropic:claude-sonnet-4-6`).
On primary failure flycanon transparently retries on the fallback
before bubbling up the 502.

---

### Answers are concise / formal / wrong tone

The default prompt is intentionally terse. Tune via the
`instructions` field on `POST /api/v1/query`:

```json
{
  "question": "Summarise the scope section.",
  "instructions": "Answer in Spanish, use bullet points, avoid passive voice."
}
```

This appends to the system prompt without changing the grounding
contract -- the model is still constrained to use only the supplied
chunks.

---

## EDA

### Subscribers aren't receiving `flycanon.knowledge` events

flycanon uses the **Postgres outbox + LISTEN/NOTIFY** pattern by
default. Events are durable; if a subscriber missed them, they're
still in `pyfly_eda_outbox`.

**Check:**

```sql
SELECT topic, count(*)
FROM pyfly_eda_outbox
WHERE published_at IS NULL
GROUP BY 1;
```

A non-zero count means events are queued but not delivered.
Common reasons:

* The `flycanon-worker` container isn't running (the worker pumps
  the outbox).
* `pg_bouncer` is in `transaction` pooling mode -- LISTEN/NOTIFY
  doesn't survive that. Use `session` mode.
* Subscribers are pointing at the wrong topic. flycanon publishes to
  `flycanon.ingest`, `flycanon.knowledge`, `flycanon.audit` (the
  topic names are configurable via `FLYCANON_*_TOPIC`).

---

### `FLYCANON_EDA_ADAPTER=redis` -- events silently dropped

Redis Streams don't survive a Redis restart unless persistence is
enabled.

**Fix.**

* Run Redis with `--appendonly yes` (the docker-compose default).
* For production, use the `postgres` adapter (default) -- the
  outbox is durable by construction.
* Or upgrade to `kafka` for cross-region replication.

---

## SDK consumers

### Java SDK: `Could not resolve dependencies for com.firefly:flycanon-sdk`

GitHub Packages requires authentication for **all** reads (even
public packages). Your Maven `~/.m2/settings.xml` needs a `github`
server entry:

```xml
<servers>
  <server>
    <id>github-firefly-operationOS</id>
    <username>your-github-username</username>
    <password>ghp_xxx_personal_access_token_with_read_packages</password>
  </server>
</servers>
```

The `<id>` must match the `<repository><id>` in your `pom.xml`. The
PAT needs at minimum the `read:packages` scope.

---

### Python SDK: `uv add https://.../flycanon_sdk-*.whl` fails with 404

The release asset URL the install snippet shows uses the **PEP 440
normalised** version (`26.5.4`), not the tag version (`v26.05.04`).
Leading zeros are stripped.

**Fix.** Use the URL from the release notes (it's pre-computed
correctly) or rebuild it:

```bash
# Wrong (tag form):
uv add https://github.com/.../v26.05.04/flycanon_sdk-26.05.04-py3-none-any.whl

# Right (PEP 440):
uv add https://github.com/.../v26.05.04/flycanon_sdk-26.5.4-py3-none-any.whl
```

---

## Performance

### High p99 on `/api/v1/search`

The hybrid retriever runs BM25 + vector + RRF in parallel, but the
top-K hydration step joins `canon_chunks` + `canon_sources`. Common
bottlenecks:

* **Missing indexes on `canon_chunks(source_id)`** -- created by
  default migrations but verify with `\d canon_chunks`.
* **pgvector HNSW parameters too aggressive** -- if you raised
  `FLYCANON_PGVECTOR_HNSW_EF_CONSTRUCTION` past 200, lower it back
  to 64-128 (default) and re-index.
* **Cold cache** -- the first query after a deploy is always slow
  because the BM25 + HNSW indexes aren't in shared_buffers. Warm
  with a synthetic query at boot.
* **Large `per_query_k`** -- if a downstream caller bumped
  `per_query_k` to 500, the BM25 channel pulls 500 rows per query.
  Lower it.

---

### Ingestion slows down past N sources

The `canon_chunks` table is the largest in a typical deployment.
After ~1M chunks:

* **HNSW index grows nonlinearly** -- expect 2-3 GB of index for
  1M chunks at 1536 dimensions.
* **`tsvector` GIN updates slow down** -- consider raising
  `maintenance_work_mem` on the Postgres side, or
  `FLYCANON_PGVECTOR_HNSW_EF_CONSTRUCTION` if you're rebuilding the
  HNSW index post-load.

For corpora past ~5M chunks consider partitioning by `domain` or
moving the dense vectors to a dedicated `qdrant` cluster
(`FLYCANON_VECTOR_STORE=qdrant`) -- the canonical store stays in
Postgres, the dense index lives where it can scale horizontally.

---

## Still stuck?

* The OpenAPI spec at `/openapi.json` is the source of truth for
  every endpoint's contract -- compare your call against it.
* Every error response is RFC 7807 ProblemDetails with a stable
  `code` field; grep the source for the code to find the failure
  site.
* The audit log (`GET /api/v1/audit`) carries every mutation with
  correlation id + actor + payload -- useful for "what happened
  during ingest" forensics.
