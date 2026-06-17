# RLM Integration — Making RLM the Default Answer Mode

- **Status:** Approved (2026-06-17)
- **Author:** Generated during brainstorming with the user
- **Integration branch:** `feat/rlm-integration` (all PRs target this; only the user merges it to `main`)

## 1. Background

Playground experiments in `mgf_playground/flycanon_experiments` established that **RLM
(Recursive Language Model)** decisively beats flycanon's current hybrid RAG pipeline on
answer quality. On FinanceBench 50/50 (81 questions, 184 whole filings):

| Metric (median) | RLM | Hybrid RAG (baseline) |
| --- | --- | --- |
| RAGAS Answer Correctness | **0.487–0.497** | 0.152 |
| Contains Answer (custom) | **0.630–0.774** | 0.037 |
| Answer Relevancy (RAGAS) | **0.621–0.775** | 0.041 |

RLM is a **code-driven agentic technique**: the model is handed the corpus as a `docs`
variable and writes Python in a persistent, sandboxed REPL to route to the right
document, read it whole, and compute exact answers (e.g. derived financial ratios). It
needs no embeddings and reasons over whole documents instead of retrieved fragments.

The reference implementation lives in
`mgf_playground/flycanon_experiments/techniques/rlm/` (`llm.py`, `repl.py`, `corpus.py`).

## 2. Goal

Make **RLM the default answer mode** in the flycanon product, controlled by environment
variables. Keep the RAG pipeline available as an explicit, opt-in mode that emits a
**deprecation warning** (to be removed in a future release).

## 3. Decisions (locked during brainstorming)

1. **Default model:** `anthropic:claude-sonnet-4-6` for RLM root/sub/answer calls
   (env-overridable). Sonnet is the current champion (`2026-06-16-rlm-sonnet`).
2. **API parity:** RLM must support full parity from the start — `POST /api/v1/query`,
   `POST /api/v1/query:stream` (SSE), and all `AnswerRequest` filters.
3. **Corpus source:** mirror **flyquery**. flyquery retains the original artifact in an
   object store (`ObjectStore` hexagonal port; LocalFs dev / S3 prod) keyed by
   `tenant/workspace/.../files/{id}.{ext}`, with a DB row holding `object_store_key` +
   `content_hash_sha256` + `size_bytes`. flycanon today discards originals and keeps only
   chunks. We add the same pattern to flycanon so RLM can read **whole documents**,
   re-extracting text on demand via flycanon's existing loaders.
4. **Integration branch:** `feat/rlm-integration`; every small piece of work is its own
   PR into this branch; the user alone merges the branch to `main`.

## 4. Architecture

A new env switch **`FLYCANON_ANSWER_MODE`** (`rlm` default | `rag`) selects how
`/api/v1/query` answers.

### Components

1. **ObjectStore port** (`core/services/storage/`) — `put/get/delete/exists`; backends
   `LocalFsObjectStore` (dev) and `S3ObjectStore` (prod). Config `FLYCANON_OBJECT_STORE_*`
   (backend, bucket/root, prefix, credentials). Mirrors flyquery's port shape.
2. **Original-artifact persistence** — Alembic migration adds
   `canon_sources.object_store_key` (nullable). `IntakeService.submit` writes the original
   document bytes to the object store and records the key, behind
   `FLYCANON_STORE_ORIGINALS` (default `true`). Re-ingest backfills existing corpora.
3. **CanonDocStore** (`core/services/query/rlm/corpus.py`) — adapts in-scope flycanon
   sources + ObjectStore + loaders into RLM's `docs` interface: `docs.keys()`,
   `docs[id]` (whole text), `docs.pages(id)`, `docs.npages(id)`. Whole-document text is
   produced by re-running the same loader used at ingest (page-structured). Scoped by
   workspace and every `AnswerRequest` filter.
4. **RLM engine** (`core/services/query/rlm/{llm,repl}.py`) — ported from the experiment,
   adapted to flycanon's Anthropic client, model-string config, and token accounting. The
   restricted CodeAct sandbox (safe builtins + `re`, `llm()`, `rlm()`, `final()`) is
   preserved. Knobs: `FLYCANON_RLM_ROOT_MODEL`/`SUB_MODEL`/`ANSWER_MODEL`
   (default `anthropic:claude-sonnet-4-6`), `FLYCANON_RLM_MAX_ITERS` (8),
   `FLYCANON_RLM_SUB_BUDGET` (12), `FLYCANON_RLM_MAX_DEPTH`.
5. **RLMAnswerService** — runs CanonDocStore + engine, returns the existing `AnswerOutput`
   shape, and maps the engine's `final(answer, filings, pages)` to `Hit` citations
   (source + page + content snippet) for API parity.
6. **Dispatch** — `AnswerService.answer()` branches on `FLYCANON_ANSWER_MODE`.
7. **Streaming + filters parity** — `/query:stream` emits SSE `status` events per REPL
   turn, then a final `answer` event and `citations`. All filters scope the corpus.
8. **RAG deprecation** — when `answer_mode=rag`: a warning log line, an
   `X-Flycanon-Deprecation` response header, and a docs note.

### Data flow (RLM mode)

```
POST /query
  -> resolve workspace + filters
  -> CanonDocStore exposes in-scope sources (lazy whole-doc text from ObjectStore + loaders)
  -> RLM REPL loop: route -> read whole filing -> extract/compute in Python -> final(...)
  -> map to AnswerResponse{ answer, citations[Hit], model, elapsed_ms }
```

## 5. PR plan (small PRs, all into `feat/rlm-integration`)

1. **ObjectStore port + LocalFs/S3 backends** + config + unit tests.
2. **`canon_sources.object_store_key`** model field + Alembic migration.
3. **Persist originals on ingest** (`IntakeService`, `FLYCANON_STORE_ORIGINALS`) + tests.
4. **CanonDocStore** adapter + tests.
5. **Port RLM engine** (`llm` + `repl`) into flycanon + unit tests (sandbox/format).
6. **RLMAnswerService** + citation mapping + tests.
7. **`FLYCANON_ANSWER_MODE`** config + dispatch in `AnswerService` + tests.
8. **Streaming parity** (`/query:stream` RLM SSE) + tests.
9. **Filter parity** (apply all filters to corpus scope) + tests.
10. **RAG deprecation** surfacing (header/log/docs) + tests.
11. **Docs** (README/config/architecture/pipeline: RLM default, env vars, deprecation).
12. **SDK touch-ups** (python + java) — only if the response gains a deprecation field.

Dependency order: 1 → 2 → 3 (corpus foundation); 5 → 6 (engine); 4 + 6 → 7 (dispatch);
7 → 8, 9, 10 (parity/deprecation); 11, 12 last.

## 6. Testing

- Per-component unit tests (ObjectStore backends, REPL sandbox/format, CanonDocStore
  scoping, citation mapping, dispatch, deprecation surface). LLM calls mocked.
- One integration test of an RLM query end-to-end against a small seeded corpus.
- Tests are plain pytest functions (no classes).

## 7. Benchmark + completion

After all PRs merge to `feat/rlm-integration`:

1. Bring up flycanon (docker-compose) with `FLYCANON_ANSWER_MODE=rlm` and
   `FLYCANON_STORE_ORIGINALS=true`.
2. Ingest the FinanceBench 50/50 corpus (184 filings) into a dedicated workspace.
3. Run the experiment harness against flycanon's RLM-mode API; evaluate with RAGAS +
   custom metrics (the harness's existing pipeline).
4. Write the report to the Windows Desktop as Markdown.
5. Open the `feat/rlm-integration` → `main` PR and **wait for the user's approval**.

> Runtime note: the RLM engine and the benchmark require `ANTHROPIC_API_KEY` at runtime.
> Implementation and unit tests (LLM mocked) do not. If the key is unavailable at
> benchmark time, the blocker is reported rather than a fabricated score.

## 8. Out of scope

- Removing the RAG pipeline (deprecation only this round).
- Re-chunking strategy changes; reranker/query-expansion changes.
- flyquery changes (referenced only as the pattern source).
