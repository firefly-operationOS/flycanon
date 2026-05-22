<div align="center">

<img src="docs/assets/logo.png" alt="flycanon" width="380" />

### **Quickstart**

Ten minutes from `git clone` to your first ingest + grounded answer.

</div>

---

Boots the full stack, ingests a sample source, and answers a
question over it — all against the mock LLM so no provider
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
- `postgres`         `pgvector/pgvector:pg16` -- canonical store +
                     dense-vector projection in one operational
                     Postgres
- `redis`            cache backend
- `mock-llm`         OpenAI-compatible stub for ingestion + RAG

Wait until the API healthcheck flips green:

```bash
task health:readiness
```

## 2. Ingest a source -- ANY format

The intake pipeline accepts any file. It hashes the bytes
(idempotency), sniffs the media type from the magic bytes, routes
the payload through the binary normaliser, then parses + chunks the
content, embeds every chunk, and indexes both the BM25 (SQLite FTS5)
and vector (pgvector) projections.

DOCX:

```bash
curl -fsS -X POST http://localhost:8500/api/v1/sources \
  -F "file=@./tests/fixtures/sample.docx" \
  -F 'metadata={"title":"Sample","domain":"process_owner"};type=application/json' \
  | jq .
```

A scanned PDF (Tesseract OCR happens server-side):

```bash
curl -fsS -X POST http://localhost:8500/api/v1/sources \
  -F "file=@./scan.pdf" \
  -F 'metadata={"title":"Scanned policy"};type=application/json' \
  | jq .
```

A ZIP archive (recursively expanded, each child re-ingested):

```bash
curl -fsS -X POST http://localhost:8500/api/v1/sources \
  -F "file=@./bundle.zip" \
  -F 'metadata={"title":"Q1 deliverables"};type=application/json' \
  | jq .
```

An `.eml` email (body + attachments decomposed, each carries
`metadata.parent_artifact`):

```bash
curl -fsS -X POST http://localhost:8500/api/v1/sources \
  -F "file=@./escalation.eml" \
  | jq .
```

The response carries a `source_id`. Poll its status:

```bash
curl -fsS http://localhost:8500/api/v1/sources/<source_id> | jq .
```

## 3. Search the corpus

Hybrid retrieval -- BM25 + dense vectors, RRF fusion, configurable
top-k.

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

A grounded "I don't know" looks like:

```json
{
  "answer": "",
  "citations": [],
  "model": "openai:gpt-4o",
  "elapsed_ms": 311
}
```

flycanon never hallucinates an answer -- if retrieval is empty, the
response is empty.

## 5. The Tier 1 / Tier 2 surfaces

The endpoints below cover the rest of the public surface. Each one
runs against the same stack you booted in step 1 -- no extra config.

### Re-ingest the same source

```bash
curl -fsS -X PUT http://localhost:8500/api/v1/sources/<source_id> \
  -F "file=@./sample-v2.docx" \
  -F 'metadata={"title":"Sample (v2)"};type=application/json' \
  | jq .
```

Preserves the row id; downstream citations follow the new content.

### Async ingest + live progress (SSE)

```bash
JOB=$(curl -fsS -X POST http://localhost:8500/api/v1/sources:async \
  -H 'Content-Type: application/json' \
  -d '{"content_base64":"'"$(base64 < ./big.pdf)"'","filename":"big.pdf"}' \
  | jq -r .id)
echo "job=$JOB"

# Stream progress -- finishes on ``completed`` or ``failed``.
curl -fsS http://localhost:8500/api/v1/jobs/$JOB/stream
```

### Knowledge graph + diff

```bash
# Add a typed edge between two canonical items.
curl -fsS -X POST http://localhost:8500/api/v1/knowledge/<id>/relations \
  -H 'Content-Type: application/json' \
  -d '{"to_item_id":"<other>","kind":"depends_on"}' | jq .

# Walk the whole-canon graph as JSON ...
curl -fsS http://localhost:8500/api/v1/knowledge:graph | jq .

# ... or as Mermaid (one curl + paste into a markdown viewer).
curl -fsS -H 'Accept: text/vnd.mermaid' \
  http://localhost:8500/api/v1/knowledge:graph

# Unified diff between two versions of an item.
curl -fsS "http://localhost:8500/api/v1/knowledge/<id>/diff?from_version=1&to_version=2" \
  | jq .
```

### Conversations + suggested follow-ups

```bash
# Start a thread.
CID=$(curl -fsS -X POST http://localhost:8500/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"onboarding"}' | jq -r .id)

# Ask the first turn -- response carries citations + turn id.
curl -fsS -X POST http://localhost:8500/api/v1/conversations/$CID/turn \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does the document say about scope?"}' | jq .

# Follow-up question; the answer agent sees the previous turn via
# pydantic-ai's ``message_history`` slot.
curl -fsS -X POST http://localhost:8500/api/v1/conversations/$CID/turn \
  -H 'Content-Type: application/json' \
  -d '{"question":"And how does that affect timelines?"}' | jq .

# Three grounded suggestions for the next thing to ask.
curl -fsS -X POST http://localhost:8500/api/v1/query/suggest \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does the document say about scope?","answer":"..."}' \
  | jq .
```

### Streaming answer (SSE)

```bash
curl -fsS -X POST http://localhost:8500/api/v1/query/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"Summarise the scope section in three sentences."}'
```

Each frame is a token; the final frame carries the full answer +
citations.

### Quality scans

```bash
# Per-item staleness scores (6h cached).
curl -fsS http://localhost:8500/api/v1/knowledge:stale | jq .

# Pairwise LLM-judged conflict scan -- confirmed conflicts land as
# candidates and as ``conflicts_with`` edges on the knowledge graph.
curl -fsS -X POST http://localhost:8500/api/v1/knowledge:detect-conflicts \
  -H 'Content-Type: application/json' \
  -d '{"domain":"compliance","min_similarity":0.85}' | jq .
```

### Billing + corpus inventory

```bash
# What did we spend today / this week / this month?
curl -fsS http://localhost:8500/api/v1/billing/summary | jq .

# Top spenders by model.
curl -fsS 'http://localhost:8500/api/v1/billing/top?dimension=model&limit=5' | jq .

# p50 / p95 / p99 latency per model.
curl -fsS http://localhost:8500/api/v1/billing/latency | jq .

# One-shot corpus + queue + cost snapshot.
curl -fsS http://localhost:8500/api/v1/stats | jq .
```

## 6. Agent surface (mint -> use)

The `/api/v1/agent/*` routes are gated by an `X-Agent-Token` header
instead of an operator JWT. Mint one (user-tier), capture the secret
ONCE, then call the agent endpoints:

```bash
# 1. Mint a token (user-tier).
curl -fsS -X POST http://localhost:8500/api/v1/agent-tokens \
  -H "X-Tenant-Id: default" -H "X-Workspace-Id: default" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-runner","scopes":["agent.sources:ingest","agent.query:run"]}'
# Response includes "token": "agt_<8hex>_<32hex>" ONCE -- store it.

# 2. Use it on the agent surface.
AGENT_TOKEN="agt_..."
curl -fsS -X POST http://localhost:8500/api/v1/agent/query \
  -H "X-Tenant-Id: default" -H "X-Workspace-Id: default" \
  -H "X-Agent-Token: $AGENT_TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"question":"Summarise the scope section in three sentences."}'
```

`Idempotency-Key` is mandatory on agent POSTs (missing key returns
`400 missing_idempotency_key`). See
[docs/api-reference.md](docs/api-reference.md#agent-surface) for the
full scope list, error codes, and all eight agent endpoints.

## 7. Tear down

```bash
task docker:down:test
```

## Where to next

- [`docs/architecture.md`](docs/architecture.md) -- the data model,
  the binary normaliser routing matrix, and the retrieval / RAG path.
- [`docs/pipeline.md`](docs/pipeline.md) -- intake -> retrieval ->
  answer with all the agentic primitives flycanon composes.
- [`docs/payload-reference.md`](docs/payload-reference.md) -- every
  REST request + response payload, with examples.
- [`docs/api-reference.md`](docs/api-reference.md) -- full endpoint
  catalogue (also at `/docs` and `/redoc` on the running service).
- [`docs/conversations.md`](docs/conversations.md) -- chat surface
  (rolling summary, message_history, suggested follow-ups).
- [`docs/async-ingest.md`](docs/async-ingest.md) -- job lifecycle +
  SSE frame format.
- [`docs/quality.md`](docs/quality.md) -- staleness + conflict scans.
- [`docs/pii.md`](docs/pii.md) -- PII guardrail policy matrix.
- [`docs/billing.md`](docs/billing.md) -- the six billing endpoints
  + what each one answers.
- [`docs/stats.md`](docs/stats.md) -- the corpus inventory snapshot.
- [`docs/eda-events.md`](docs/eda-events.md) -- the topics flycanon
  publishes on `flycanon.ingest`, `flycanon.knowledge`,
  `flycanon.audit`.
- [`sdks/python/QUICKSTART.md`](sdks/python/QUICKSTART.md) -- async
  Python SDK tour.
- [`sdks/java/QUICKSTART.md`](sdks/java/QUICKSTART.md) -- Spring Boot
  Java SDK tour.
