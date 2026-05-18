<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Documentation**

</div>

---

The complete reference set for **flycanon**. Start with the top-level
[QUICKSTART.md](../QUICKSTART.md) for your first ingest + answer, the
main [README.md](../README.md) for the elevator pitch and the
walk-through, and [payload-reference.md](payload-reference.md) for
the authoritative wire-payload guide. Come here when you need a
specific corner of the system.

---

## Reading paths

Pick the entry point that matches what you're trying to do:

### "I just want to call the API"

1. [**QUICKSTART.md**](../QUICKSTART.md) — ten minutes from clone to
   your first ingest, search, and grounded answer, HTTP-only, mock
   LLM, no API keys.
2. [**payload-reference.md**](payload-reference.md) — composing
   the request: every wire payload (sources, knowledge, candidates,
   search, query, taxonomy, audit) plus the RFC 7807 error envelope.
3. [**api-reference.md**](api-reference.md) — every endpoint, header,
   query parameter, DTO, and error code.
4. [**glossary.md**](glossary.md) — terms the API uses (canonical
   knowledge, candidate, supersession, provenance, …).

### "I'm subscribing to the EDA surface"

1. [**eda-events.md**](eda-events.md) — the three topics flycanon
   publishes (`flycanon.ingest`, `flycanon.knowledge`,
   `flycanon.audit`), the payloads on each event, the durable
   Postgres outbox.
2. [**architecture.md** § DI wiring](architecture.md#di-wiring) —
   how `pyfly.eda.EventPublisher` is injected into every audited
   service.

### "I want to understand how it works"

1. [**architecture.md**](architecture.md) — the data model, the
   binary-normaliser routing matrix, the pluggable retrieval backend
   matrix, the layer diagram, the dependency arrows.
2. [**pipeline.md**](pipeline.md) — source intake → retrieval →
   answer end-to-end, with the agentic primitives flycanon composes.

### "I'm extending the service"

1. [**architecture.md** § Universal binary normaliser](architecture.md#universal-binary-normaliser) — how the routing matrix dispatches on media type; where to plug a new format.
2. [**architecture.md** § Pluggable retrieval backends](architecture.md#pluggable-retrieval-backends) — adding a new `VectorStoreProtocol` adapter.
3. [**pipeline.md**](pipeline.md) — the orchestrator and the stages it composes.

### "I'm running this in production"

1. [**deployment.md**](deployment.md) — reference topologies, env vars, OCR engines, Office conversion, embedding providers, auth, observability, sizing.
2. [**cicd.md**](cicd.md) — the three GitHub Actions workflows (PR gate, Docker publish, SDK publish), release cookbook, required secrets.
3. [**troubleshooting.md**](troubleshooting.md) — symptom → root cause → fix for the common failure modes (embeddings, OCR, pgvector dim mismatch, EDA, SDK install, performance).

---

## Document catalogue

| Document                                          | Read it when…                                                                                                                  |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| [../QUICKSTART.md](../QUICKSTART.md)              | You want your first ingest + search + answer in ten minutes (HTTP / curl).                                                     |
| [architecture.md](architecture.md)                | You need the data model, the binary-normaliser routing matrix, the pluggable retrieval backend matrix, the dependency arrows.  |
| [pipeline.md](pipeline.md)                        | You're touching the orchestrator, adding a new stage, or chasing a slow ingest.                                                |
| [api-reference.md](api-reference.md)              | You're integrating with the HTTP API and need every endpoint, shape, and status code.                                          |
| [payload-reference.md](payload-reference.md)      | You're composing the request payload — every field, option, and example.                                                       |
| [eda-events.md](eda-events.md)                    | You're subscribing to the `flycanon.ingest` / `flycanon.knowledge` / `flycanon.audit` topics.                                  |
| [conversations.md](conversations.md)              | You're building chat-style UX on flycanon -- threads, turns, rolling summary, suggested follow-ups.                            |
| [async-ingest.md](async-ingest.md)                | You're enqueuing large / bulk ingests and streaming progress via SSE.                                                          |
| [quality.md](quality.md)                          | You're running staleness scans or conflict detection on the canon.                                                             |
| [pii.md](pii.md)                                  | You need to understand the PII guardrail (scanner, policy, redaction, findings, RFC 7807 violation shape).                     |
| [billing.md](billing.md)                          | You're tracking LLM cost / latency -- the six `/api/v1/billing/*` endpoints, the recorded fields, when to use each.            |
| [stats.md](stats.md)                              | You're rendering a corpus dashboard -- the one-shot `/api/v1/stats` snapshot.                                                  |
| [deployment.md](deployment.md)                    | You're running this in production -- env vars, topologies, OCR engines, embedding providers, auth, observability, sizing.       |
| [cicd.md](cicd.md)                                | You're cutting a release or wiring CI/CD -- PR gate, Docker publish, SDK publish, secrets.                                      |
| [troubleshooting.md](troubleshooting.md)          | The service / ingest / search / answer surface is misbehaving -- symptom → root cause → fix.                                    |
| [glossary.md](glossary.md)                        | You need a precise definition for a term the API or docs use.                                                                  |
| [../sdks/python/README.md](../sdks/python/README.md) | You're integrating from Python (async-first SDK, Pydantic typing).                                                          |
| [../sdks/java/README.md](../sdks/java/README.md)  | You're integrating from Java / Spring Boot (`com.firefly`, Java 25, `@AutoConfiguration`).                                     |

---

## Cross-cutting topics

Where to read about each topic that spans multiple documents:

| Topic                                  | Primary                                                                                       | Secondary                                                                          |
| -------------------------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Universal ingestion (any file format)  | [architecture.md § Universal binary normaliser](architecture.md#universal-binary-normaliser)  | [pipeline.md](pipeline.md), [README.md § Universal ingestion](../README.md#universal-ingestion) |
| Backend-agnostic retrieval             | [architecture.md § Pluggable retrieval backends](architecture.md#pluggable-retrieval-backends) | [README.md § Backend-agnostic retrieval](../README.md#backend-agnostic-retrieval)  |
| Hybrid retrieval (BM25 + vectors + RRF) | [pipeline.md](pipeline.md), [api-reference.md § /search](api-reference.md#query)              | [architecture.md § Layers](architecture.md#layers)                                  |
| Grounded RAG answers (no hallucinations) | [pipeline.md](pipeline.md), [api-reference.md § /query](api-reference.md#query)              | [README.md § What you get back](../README.md#what-you-get-back)                    |
| Provenance graph                       | [api-reference.md § /provenance](api-reference.md#provenance)                                  | [glossary.md § Provenance](glossary.md)                                            |
| Knowledge graph (typed edges + Mermaid) | [api-reference.md § /knowledge:graph](api-reference.md)                                       | [payload-reference.md § Knowledge graph](payload-reference.md)                      |
| Async ingest (jobs + SSE)              | [async-ingest.md](async-ingest.md)                                                            | [api-reference.md § Async ingest jobs](api-reference.md)                            |
| Multi-turn conversations + streaming   | [conversations.md](conversations.md)                                                          | [api-reference.md § Conversations](api-reference.md)                                |
| Knowledge quality (staleness + conflicts) | [quality.md](quality.md)                                                                  | [api-reference.md § Knowledge](api-reference.md)                                    |
| PII guardrail                          | [pii.md](pii.md)                                                                              | [payload-reference.md § PII](payload-reference.md)                                  |
| Billing + cost stream                  | [billing.md](billing.md)                                                                      | [api-reference.md § Billing](api-reference.md)                                      |
| Corpus inventory snapshot              | [stats.md](stats.md)                                                                          | [api-reference.md § Inventory](api-reference.md)                                    |
| EDA / typed event envelopes            | [eda-events.md](eda-events.md)                                                                | [architecture.md § DI wiring](architecture.md#di-wiring)                            |
| Append-only audit log                  | [api-reference.md § /audit](api-reference.md#audit)                                            | [architecture.md § Data model](architecture.md#data-model)                         |
| W3C trace context                      | [architecture.md § Cross-cutting concerns](architecture.md#cross-cutting-concerns)            | [api-reference.md](api-reference.md)                                               |
| RFC 7807 error envelope                | [payload-reference.md § ProblemDetails](payload-reference.md)                                 | [api-reference.md](api-reference.md)                                               |

---

## Generating the OpenAPI spec

The machine-readable OpenAPI 3.1 document is generated from the same
DTOs documented here. Two paths:

```bash
# Against a running service:
curl -s http://localhost:8500/openapi.json | jq

# Or via the task target (writes to ./openapi.json):
task openapi
```

The Swagger UI at `/docs` and Redoc at `/redoc` both browse the same
spec.
