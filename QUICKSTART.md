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

Every tenant route needs `X-Tenant-Id` + `X-Workspace-Id` (lowercase
slugs) and, once `FLYCANON_API_KEYS` is set, the platform key. The test
stack ships without a key, so only the scope headers are needed here;
export them once so the snippets below stay short:

```bash
export H=(-H 'X-Tenant-Id: acme' -H 'X-Workspace-Id: ws-demo' -H 'Content-Type: application/json')
# with FLYCANON_API_KEYS=change-me you would add:  -H 'X-API-Key: change-me'
```

## 2. Ingest a source -- ANY format

The intake pipeline accepts any file. It hashes the bytes
(idempotency), sniffs the media type from the magic bytes, routes
the payload through the binary normaliser, then parses + chunks the
content, embeds every chunk, and indexes both the BM25 (Postgres
`tsvector` + GIN) and dense vector (pgvector) projections.

The endpoint is **JSON only**: the bytes travel base64-encoded in
`content_base64` (there is no multipart form). A small helper keeps
the curls readable:

```bash
submit() {  # submit <file> [<metadata-json>]
  jq -n --arg f "$(basename "$1")" --arg b "$(base64 < "$1" | tr -d '\n')" \
        --argjson m "${2:-{\}}" '{filename:$f, content_base64:$b, metadata:$m}'
}
```

DOCX:

```bash
submit ./tests/fixtures/sample.docx '{"title":"Sample","domain":"process"}' \
  | curl -fsS -X POST http://localhost:8500/api/v1/sources "${H[@]}" -d @- | jq .
```

A scanned PDF (Tesseract OCR happens server-side):

```bash
submit ./scan.pdf '{"title":"Scanned policy"}' \
  | curl -fsS -X POST http://localhost:8500/api/v1/sources "${H[@]}" -d @- | jq .
```

A ZIP archive (recursively expanded, each child re-ingested):

```bash
submit ./bundle.zip '{"title":"Q1 deliverables"}' \
  | curl -fsS -X POST http://localhost:8500/api/v1/sources "${H[@]}" -d @- | jq .
```

An `.eml` email (body + attachments decomposed, each carries
`metadata.parent_artifact`):

```bash
submit ./escalation.eml \
  | curl -fsS -X POST http://localhost:8500/api/v1/sources "${H[@]}" -d @- | jq .
```

`metadata.domain` and `metadata.jurisdiction` are closed enums
(`legal`, `compliance`, `process`, `hr`, `engineering`, ... and
`GLOBAL`, `EU`, `ES`, `US`, ...); free-form labels go in
`metadata.tags`. The response carries the source `id`. Poll its status:

```bash
curl -fsS http://localhost:8500/api/v1/sources/<source_id> "${H[@]}" | jq .
```

Remove it again (index, chunks and the stored original all go):

```bash
curl -fsS -X DELETE http://localhost:8500/api/v1/sources/<source_id> "${H[@]}"
```

## 3. Search the corpus

Hybrid retrieval -- BM25 + dense vectors, RRF fusion, configurable
top-k.

```bash
curl -fsS -X POST http://localhost:8500/api/v1/search "${H[@]}" \
  -d '{"query":"what does the document say about scope","top_k":5}' \
  | jq .
```

Each hit carries `chunk_id`, `source_id`, the matching `content`, and
the fused `score`.

## 4. Ask a question

RAG answer with citations.

```bash
curl -fsS -X POST http://localhost:8500/api/v1/query "${H[@]}" \
  -d '{"question":"Summarise the scope section in three sentences."}' \
  | jq .
```

The response shape:

```json
{
  "answer": "...",
  "citations": [
    {"chunk_id": "<source_id>#p3", "source_id": "...", "source_title": "Sample", "page": 3, "score": 1.0}
  ],
  "model": "anthropic:claude-sonnet-4-6",
  "elapsed_ms": 842,
  "no_answer": false
}
```

A grounded "I don't know" looks like:

```json
{
  "answer": "The corpus does not cover this question.",
  "citations": [],
  "model": "anthropic:claude-sonnet-4-6",
  "elapsed_ms": 311,
  "no_answer": true
}
```

flycanon never hallucinates an answer -- branch on `no_answer`, not on
the text. With the RLM engine (the default) citations are page
pointers (`chunk_id = "<source_id>#p<page>"`); with the deprecated RAG
engine they are real chunk ids and the response carries an
`X-Flycanon-Deprecation` header.

## 5. The Tier 1 / Tier 2 surfaces

The endpoints below cover the rest of the public surface. Each one
runs against the same stack you booted in step 1 -- no extra config.

### Re-ingest the same source

```bash
submit ./sample-v2.docx '{"title":"Sample (v2)"}' \
  | curl -fsS -X PUT http://localhost:8500/api/v1/sources/<source_id> "${H[@]}" -d @- | jq .
```

Preserves the row id; downstream citations follow the new content.

### Async ingest + live progress (SSE)

```bash
JOB=$(submit ./big.pdf \
  | curl -fsS -X POST 'http://localhost:8500/api/v1/sources?mode=async' "${H[@]}" -d @- \
  | jq -r .id)
echo "job=$JOB"

# Stream progress -- closes on ``succeeded`` or ``failed``. Each event
# frame carries an ``id:``; reconnect with ``?after_id=<id>`` (or let
# EventSource send ``Last-Event-ID``) to resume instead of replaying.
curl -fsS -N http://localhost:8500/api/v1/ingest-jobs/$JOB/stream "${H[@]}"
```

Add `&callback_url=https://hooks.example.com/flycanon` to be POSTed the
outcome instead; with `FLYCANON_WEBHOOK_SECRET` set the request carries
an `X-Flycanon-Signature` HMAC (see [docs/async-ingest.md](docs/async-ingest.md)).
Callback hosts that are not globally routable (private, loopback, link-local, `100.64.0.0/10`) are refused.

### Tear a workspace down

```bash
# Every source (index, chunks, originals), knowledge, candidates,
# conversations, jobs and cost rows of the workspace -- then close it.
curl -fsS -X POST http://localhost:8500/api/v1/workspaces/ws-demo:purge "${H[@]}" | jq .
```

### Knowledge graph + diff

```bash
# Add a typed edge between two canonical items.
curl -fsS -X POST http://localhost:8500/api/v1/knowledge/<id>/relations "${H[@]}" \
  -d '{"to_item_id":"<other>","kind":"depends_on"}' | jq .

# Walk the whole-canon graph as JSON ...
curl -fsS http://localhost:8500/api/v1/knowledge:graph "${H[@]}" | jq .

# ... or as Mermaid (one curl + paste into a markdown viewer).
curl -fsS -H 'Accept: text/vnd.mermaid' "${H[@]}" \
  http://localhost:8500/api/v1/knowledge:graph

# Unified diff between two versions of an item.
curl -fsS "http://localhost:8500/api/v1/knowledge/<id>/diff?from_version=1&to_version=2" "${H[@]}" \
  | jq .
```

### Conversations + suggested follow-ups

```bash
# Start a thread.
CID=$(curl -fsS -X POST http://localhost:8500/api/v1/conversations "${H[@]}" \
  -d '{"title":"onboarding"}' | jq -r .id)

# Ask the first turn -- response carries citations + turn id.
curl -fsS -X POST http://localhost:8500/api/v1/conversations/$CID/turn "${H[@]}" \
  -d '{"question":"What does the document say about scope?"}' | jq .

# Follow-up question; turns run on the same engine as ``/query`` and
# the model sees the previous turns as conversation history.
curl -fsS -X POST http://localhost:8500/api/v1/conversations/$CID/turn "${H[@]}" \
  -d '{"question":"And how does that affect timelines?"}' | jq .

# Three grounded suggestions for the next thing to ask.
curl -fsS -X POST http://localhost:8500/api/v1/query/suggest "${H[@]}" \
  -d '{"question":"What does the document say about scope?","answer":"..."}' \
  | jq .
```

### Streaming answer (SSE)

```bash
curl -fsS -N -X POST http://localhost:8500/api/v1/query/stream "${H[@]}" \
  -d '{"question":"Summarise the scope section in three sentences."}'
```

Frames are `event: status` (RLM reasoning turns) or `event: hit`
(RAG), then one `event: final` with the full answer + citations, or
`event: error`.

### Quality scans

```bash
# Per-item staleness scores (6h cached).
curl -fsS http://localhost:8500/api/v1/knowledge:stale "${H[@]}" | jq .

# Pairwise LLM-judged conflict scan -- confirmed conflicts land as
# candidates and as ``conflicts_with`` edges on the knowledge graph.
curl -fsS -X POST http://localhost:8500/api/v1/knowledge:detect-conflicts "${H[@]}" \
  -d '{"domain":"compliance","min_similarity":0.85}' | jq .
```

### Billing + corpus inventory

```bash
# What did we spend today / this week / this month?
curl -fsS http://localhost:8500/api/v1/billing/summary "${H[@]}" | jq .

# Top spenders by model.
curl -fsS 'http://localhost:8500/api/v1/billing/top?dimension=model&limit=5' "${H[@]}" | jq .

# p50 / p95 / p99 latency per model.
curl -fsS http://localhost:8500/api/v1/billing/latency "${H[@]}" | jq .

# One-shot corpus + queue + cost snapshot.
curl -fsS http://localhost:8500/api/v1/stats "${H[@]}" | jq .
```

## 6. Agent surface (mint -> use)

The `/api/v1/agent/*` routes are gated by an `X-Agent-Token` header
instead of an operator JWT. Real deployments don't mint tokens
against the `default/default` scope -- create a real workspace
first, then mint against it.

```bash
# 1. Create a tenant-scoped workspace.
curl -fsS -X POST http://localhost:8500/api/v1/workspaces \
  -H "X-Tenant-Id: acme" -H "X-Workspace-Id: ws-first" \
  -H "Content-Type: application/json" \
  -d '{
        "id": "ws-first",
        "name": "Acme onboarding",
        "scope": "onboarding"
      }' | jq .
# Subsequent requests against acme/ws-first carry both headers.

# 2. Mint an agent token bound to that scope (user-tier).
curl -fsS -X POST http://localhost:8500/api/v1/agent-tokens \
  -H "X-Tenant-Id: acme" -H "X-Workspace-Id: ws-first" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-runner","scopes":["agent.sources:ingest","agent.query:run"]}'
# Response includes "token": "agt_<8hex>_<32hex>" ONCE -- store it.

# 3. Use it on the agent surface.
AGENT_TOKEN="agt_..."
curl -fsS -X POST http://localhost:8500/api/v1/agent/query \
  -H "X-Tenant-Id: acme" -H "X-Workspace-Id: ws-first" \
  -H "X-Agent-Token: $AGENT_TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"question":"Summarise the scope section in three sentences."}'
```

`Idempotency-Key` is mandatory on agent POSTs / PUTs / DELETEs (missing
key returns `400 missing_idempotency_key`). An agent-tier request that
carries `X-Agent-Token` never needs the platform API key -- the token is
its credential -- whereas steps 1 and 2 (user tier) do once
`FLYCANON_API_KEYS` is configured. See
[docs/api-reference.md](docs/api-reference.md#agent-surface) for the
full scope list, error codes, and all ten agent endpoints.

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
