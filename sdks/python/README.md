<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Python SDK**

</div>

---

Async-first Python client for the
[flycanon](https://github.com/firefly-operationOS/flycanon) Operational
Knowledge Repository service.

- Pydantic-typed request + response models.
- httpx under the hood -- pooled connections, retries, timeouts you
  control.
- Carries the firefly four-header wire contract
  (`X-Tenant-Id` / `X-Workspace-Id` / `X-Correlation-Id` /
  `X-Agent-Token`) on every outbound request.
- No service dependency -- the SDK ships its own Pydantic schemas so
  it can be installed alongside any client codebase without pulling
  the framework.

## Wire-contract compatibility

Compatible with **flycanon service version `26.5.x`**.

| SDK | Service |
|-----|---------|
| `26.5.7` | `26.5.x` |

## Install

```bash
uv add flycanon-sdk==26.5.7
# or
pip install flycanon-sdk==26.5.7
```

## Quick start

```python
import asyncio
from flycanon_sdk import AnswerRequest, CanonClient

async def main() -> None:
    async with CanonClient(
        base_url="https://canon.example.com",
        tenant_id="acme",
        workspace_id="ws-prod",
        agent_token="canon_xxxxx",
    ) as client:
        result = await client.agent.query(
            AnswerRequest(question="What's our retention policy?"),
            idempotency_key="example-001",
        )
        print(result.answer)

asyncio.run(main())
```

See [QUICKSTART.md](QUICKSTART.md) for the longer five-minute tour.

## Constructor reference

`CanonClient(...)` -- all kwargs are keyword-only.

**Required**

| kwarg | type | description |
|-------|------|-------------|
| `base_url` | `str` | Service base URL (e.g. `https://canon.example.com`). |

**Optional wire-contract headers**

| kwarg | type | header sent | notes |
|-------|------|-------------|-------|
| `tenant_id` | `str \| None` | `X-Tenant-Id` | Required by the service on every route; pass it here so you don't have to thread it through every call. |
| `workspace_id` | `str \| None` | `X-Workspace-Id` | Required by the service on every route. |
| `correlation_id` | `str \| None` | `X-Correlation-Id` | Sticky per-request id. Usually you'll override this per call. |
| `agent_token` | `str \| None` | `X-Agent-Token` | Only meaningful on `/api/v1/agent/*` routes. |

The SDK only injects a header when you pass a value -- it does not
pre-validate; the service rejects missing tenant/workspace at the
boundary.

**Advanced**

| kwarg | type | description |
|-------|------|-------------|
| `api_key` | `str \| None` | Bearer token sent as `Authorization: Bearer ...`. |
| `timeout` | `float` | httpx timeout in seconds (default `60`). |
| `client` | `httpx.AsyncClient \| None` | Caller-supplied client to reuse. When provided, the SDK does not close it on exit. |
| `headers` | `dict[str, str] \| None` | Extra headers merged into every request. |

## Method catalog

### Workspace CRUD (`/api/v1/workspaces`, user-tier)

| Method | Description |
|--------|-------------|
| `create_workspace(spec: WorkspaceCreate) -> WorkspaceSpec` | Create a workspace under the caller's tenant. |
| `list_workspaces() -> list[WorkspaceSummary]` | List every workspace owned by the caller's tenant (`created_at DESC`). |
| `get_workspace(workspace_id) -> WorkspaceSpec` | Fetch a single workspace by id. |
| `update_workspace(workspace_id, patch: WorkspaceUpdate) -> WorkspaceSpec` | Sparse PATCH; only present fields are applied. |
| `close_workspace(workspace_id) -> WorkspaceSpec` | Terminal lifecycle transition (`status=closed`, `closed_at=now()`). Idempotent. |

### Agent token CRUD (`/api/v1/agent-tokens`, user-tier)

| Method | Description |
|--------|-------------|
| `mint_agent_token(request: AgentTokenMintRequest) -> AgentTokenCreated` | Mint a new token. Raw `token` is returned **once**. |
| `list_agent_tokens() -> list[AgentTokenSummary]` | List tokens for the caller's tenant (newest first). Secret omitted. |
| `revoke_agent_token(token_id) -> None` | Revoke a token. Idempotent. |

Agent-tier callers (authenticated with `X-Agent-Token`) are refused
on the mint path with `403 agent_cannot_mint`.

### Agent surface (`/api/v1/agent/*`, agent-tier, exposed via `client.agent`)

Every POST on this surface mandates a non-empty `idempotency_key`
keyword argument. The SDK rejects empty / whitespace values locally
with `ValueError` before the round-trip.

| Method | Description |
|--------|-------------|
| `agent.ingest_source(spec, *, idempotency_key) -> SourceRecord` | Submit a source for intake. Scope: `agent.sources:ingest`. |
| `agent.get_source(source_id) -> SourceRecord` | Read a source. Scope: `agent.sources:read`. |
| `agent.query(request, *, idempotency_key) -> AnswerResponse` | Grounded RAG answer with citations. Scope: `agent.query:run`. |
| `agent.query_stream(request, *, idempotency_key) -> AsyncIterator[IngestJobEvent]` | SSE: `hit` frames + terminal `final`. Scope: `agent.query:run`. |
| `agent.search(request, *, idempotency_key) -> SearchResponse` | Hybrid retrieval, no LLM. Scope: `agent.query:run`. |
| `agent.get_knowledge(item_id) -> KnowledgeItem` | Fetch a single knowledge item. Scope: `agent.knowledge:read`. |
| `agent.get_provenance(item_id, *, version=None) -> Provenance` | Citation graph for `(item_id, version)`. Scope: `agent.knowledge:read`. |
| `agent.propose_candidates(request, *, idempotency_key) -> list[CandidateRecord]` | Propose candidates from an existing source. Scope: `agent.candidates:propose`. |

### User-tier surface (selected highlights)

The `CanonClient` exposes every public flycanon endpoint as a typed
async method. Highlights:

```python
# Bulk + async + replace intake
await client.submit_sources_bulk([payload_a, payload_b])
job = await client.submit_source_async(payload)
async for event in client.stream_job(job.id):
    print(event.event, event.data)
await client.replace_source(source_id, new_payload)

# Knowledge graph + diff
diff = await client.get_diff(item_id, from_version=1, to_version=2)
relations = await client.list_relations(item_id)
await client.add_relation(item_id, CreateRelationRequest(
    to_item_id=other_id, kind="depends_on",
))
graph = await client.get_graph(domain="compliance")
mermaid_str = await client.get_graph_mermaid(domain="compliance")

# Conversations -- pydantic-ai message_history wired underneath
conv = await client.create_conversation(CreateConversationRequest(title="t"))
turn = await client.add_turn(conv.id, CreateConversationTurnRequest(
    query="What about scope?",
))
suggestions = await client.suggest_questions(conv.id)

# Streamed user-tier answer (token-by-token SSE)
async for frame in client.stream_answer("Summarise the scope section."):
    if frame.event == "token":
        print(frame.data.get("text"), end="", flush=True)

# Quality scans
stale = await client.scan_stale()
report = await client.detect_conflicts(ConflictScanRequest(
    domain="compliance", min_similarity=0.85,
))

# Billing + corpus inventory
summary = await client.billing_summary()
top = await client.billing_top(dimension="model", limit=5)
latency = await client.billing_latency(group_by=["model"])
snapshot = await client.stats()
```

Every method returns a Pydantic model (or a typed `AsyncIterator` for
the SSE streams) -- IDE autocomplete works out of the box.

## Idempotency-Key

The four agent-tier POSTs (`ingest_source`, `query`, `query_stream`,
`search`, `propose_candidates`) **require** the `Idempotency-Key`
header on the wire. The SDK exposes that as a mandatory
`idempotency_key=` keyword argument:

```python
await client.agent.ingest_source(spec, idempotency_key="ingest-2026-05-22-001")
```

**Contract.** The first call with a given key persists the response.
Subsequent calls with the same key + same body return the **same**
response body byte-for-byte (server-side dedup). Pass a different
body with the same key and the service rejects the retry as a
collision.

**Local validation.** The SDK rejects empty / whitespace keys with a
`ValueError` before the request goes out so you don't pay a round-trip
to discover you forgot to pass one.

```python
await client.agent.query(req, idempotency_key="")
# ValueError: idempotency_key is required for agent-tier POSTs and must be a non-empty string
```

## Error catalog

Every typed exception inherits from `CanonAPIError`, which itself
inherits from `CanonError`. Catch the subclass to handle a specific
failure; catch `CanonAPIError` to handle every API error generically.

| Exception | HTTP | `code` (RFC 7807) |
|-----------|------|--------------------|
| `MissingIdempotencyKey` | 400 | `missing_idempotency_key` |
| `ValidationError` | 400 | `invalid_request` (carries `.errors: list[FieldError]`) |
| `MissingAgentToken` | 401 | `missing_agent_token` |
| `InvalidAgentToken` | 403 | `invalid_agent_token` |
| `AgentTokenExpired` | 403 | `agent_token_expired` |
| `AgentWorkspaceNotInAllowlist` | 403 | `agent_workspace_not_in_allowlist` |
| `AgentScopeDenied` | 403 | `agent_scope_denied` |
| `AgentCannotMint` | 403 | `agent_cannot_mint` |
| `CanonAPIError` | any | catch-all for unrecognised codes |
| `CanonConnectionError` | -- | network / timeout / TLS (no wire envelope) |

```python
from flycanon_sdk import (
    AgentScopeDenied,
    CanonAPIError,
    MissingIdempotencyKey,
)

try:
    await client.agent.ingest_source(spec, idempotency_key="k-001")
except MissingIdempotencyKey:
    ...
except AgentScopeDenied:
    ...
except CanonAPIError as exc:  # catch-all
    print(exc.status_code, exc.code, exc.title)
```

Every exception carries `status_code`, `code`, `title`, `detail`,
`extensions`, `payload`, and `errors`. `errors` is a list of
`FieldError(code, path, message)` parsed from the service's
`ProblemDetail.errors` array -- most useful on `ValidationError`.

## Workspace lifecycle events

flycanon emits three event types on the `canon.workspaces.v1` topic.
The SDK ships the typed DTOs so consumers can deserialise event
payloads directly; the SDK itself does not subscribe -- wire the
topic with your own EDA stack (Kafka / RabbitMQ / etc.) and use the
DTOs to parse `payload` bodies.

```python
from flycanon_sdk import (
    CANON_WORKSPACES_TOPIC,
    WorkspaceCreated,
    WorkspaceDeleted,
    WorkspaceUpdated,
)

assert CANON_WORKSPACES_TOPIC == "canon.workspaces.v1"

# Inside your EDA consumer:
def handle(raw_payload: dict) -> None:
    event_type = raw_payload["event_type"]
    if event_type == "workspace.created":
        evt = WorkspaceCreated.model_validate(raw_payload)
    elif event_type == "workspace.updated":
        evt = WorkspaceUpdated.model_validate(raw_payload)
    elif event_type == "workspace.deleted":
        evt = WorkspaceDeleted.model_validate(raw_payload)
    else:
        return
    print(evt.tenant_id, evt.workspace_id, evt.occurred_at)
```

Every event carries `tenant_id`, `workspace_id`, `occurred_at`,
`event_type`, plus per-event payload fields. `workspace.deleted`
corresponds to `POST /api/v1/workspaces/{id}:close` -- flycanon's
terminal lifecycle transition (the row is preserved for audit; the
canonical event name matches the lifecycle vocabulary other services
use).

## Additional resources

- [CHANGELOG.md](CHANGELOG.md) -- wire-contract history per release.
- `openapi.json` -- full route list (served by the running service
  at `/openapi.json`).
- Consumers guide -- TBD, will live at
  [`flycanon/docs/consumers.md`](../../docs/consumers.md).

## License

Apache-2.0 -- see [LICENSE](LICENSE).
