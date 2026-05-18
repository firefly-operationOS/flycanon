# flycanon quickstart

Five-minute tour. Boots the full stack, ingests a sample source, and
answers a question over it -- all against the mock LLM so no provider
credentials are required.

## 0. Prerequisites

- Docker + Docker Compose v2
- [Task](https://taskfile.dev/installation/) (the runner)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (only
  needed for the host-side dev loop)

## 1. Boot the integration stack

```bash
task docker:up:test
```

This brings up:

- `flycanon-api`     on `http://localhost:8500`
- `flycanon-worker`  consuming `flycanon.ingest` events
- `postgres`         persistence + EDA outbox
- `redis`            cache backend
- `mock-llm`         OpenAI-compatible stub for ingestion + RAG

Wait until the API healthcheck flips green:

```bash
task health:readiness
```

## 2. Ingest a source

Upload any DOCX, PDF, HTML, Markdown, or TXT file. The intake pipeline
hashes the bytes (idempotency), parses + chunks the content, embeds
every chunk, and indexes both the BM25 (SQLite FTS5) and vector
(SQLite-vec) projections.

```bash
curl -fsS -X POST http://localhost:8500/api/v1/sources \
  -F "file=@./tests/fixtures/sample.docx" \
  -F 'metadata={"title":"Sample","domain":"process_owner"};type=application/json' \
  | jq .
```

The response carries a `source_id`. Poll its status:

```bash
curl -fsS http://localhost:8500/api/v1/sources/<source_id> | jq .
```

## 3. Search the corpus

Hybrid retrieval -- BM25 + vectors, RRF fusion, configurable top-k.

```bash
curl -fsS -X POST http://localhost:8500/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"what does the document say about scope","top_k":5}' \
  | jq .
```

Each hit carries `chunk_id`, `source_id`, the matching `content`, and
the fused `score`.

## 4. Ask a question

RAG answer with citations.

```bash
curl -fsS -X POST http://localhost:8500/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Summarise the scope section in three sentences."}' \
  | jq .
```

The response shape:

```json
{
  "answer": "...",
  "citations": [
    {"chunk_id": "...", "source_id": "...", "score": 0.62}
  ],
  "model": "openai:gpt-4o",
  "elapsed_ms": 842
}
```

## 5. Tear down

```bash
task docker:down:test
```

## Where to next

- [`docs/architecture.md`](docs/architecture.md) -- the data model, the
  ingestion pipeline, and the retrieval / RAG path.
- [`docs/payload-reference.md`](docs/payload-reference.md) -- every
  REST request + response payload, with examples.
- [`docs/api-reference.md`](docs/api-reference.md) -- full endpoint
  catalogue (also at `/docs` and `/redoc` on the running service).
- [`sdks/python/QUICKSTART.md`](sdks/python/QUICKSTART.md) -- async
  Python SDK tour.
- [`sdks/java/QUICKSTART.md`](sdks/java/QUICKSTART.md) -- Java SDK
  tour.
