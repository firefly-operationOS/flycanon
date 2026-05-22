<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Java SDK** &nbsp;&middot;&nbsp; Spring Boot 3.5.9 &nbsp;&middot;&nbsp; Java 25

</div>

---

Spring-Boot-native Java client for the
[flycanon](https://github.com/firefly-operationOS/flycanon) Operational
Knowledge Repository service.

- **Java 25** (LTS) / **Spring Boot 3.5.9** / **Spring Framework 6.2**.
- Two clients side by side:
  - **`CanonClient`** -- synchronous, blocking; built on Spring's
    `RestClient` (the new default that replaces `RestTemplate` in
    Spring 6.1+) and Jackson. Use from plain Spring MVC / Servlet
    stacks.
  - **`ReactiveCanonClient`** -- non-blocking; built on `WebClient`
    + Reactor Netty. Returns `Mono<T>` for unary methods and
    `Flux<StreamFrame>` for the SSE streams. Use from Spring WebFlux
    or any reactive chain.
- Carries the firefly four-header wire contract
  (`X-Tenant-Id` / `X-Workspace-Id` / `X-Correlation-Id` /
  `X-Agent-Token`) on every outbound request.
- Ships an `@AutoConfiguration` for each variant; the blocking bean
  is wired by default, the reactive bean is opt-in via
  `flycanon.reactive-auto-configure=true`.
- `groupId = com.firefly`. Apache-2.0.

## Wire-contract compatibility

Compatible with **flycanon service version `26.5.x`**.

| SDK | Service |
|-----|---------|
| `26.5.7` | `26.5.x` |

## Install

```xml
<dependency>
  <groupId>com.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.7</version>
</dependency>
```

That's the blocking client. The SDK declares `spring-boot-starter`
and `spring-web` as compile dependencies, so a Spring Boot 3.5.x
application picks up everything it needs transitively.

To use the **reactive** client too, add Spring WebFlux:

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-webflux</artifactId>
</dependency>
```

The SDK declares `spring-webflux` + `reactor-netty-http` as
`<optional>true</optional>` so consumers that don't want the
reactive variant don't pay for the reactor / netty fat.

## Quick start

With Spring Boot, set the four wire-contract properties in
`application.yml`:

```yaml
flycanon:
  base-url: https://canon.example.com
  tenant-id: acme
  workspace-id: ws-prod
  agent-token: ${FLYCANON_AGENT_TOKEN}
```

Then inject the bean and call:

```java
import com.firefly.flycanon.sdk.CanonClient;
import com.firefly.flycanon.sdk.model.Models;
import org.springframework.stereotype.Service;

@Service
public class CopilotService {

    private final CanonClient canon;

    public CopilotService(CanonClient canon) {
        this.canon = canon;
    }

    public String askAgent(String question) {
        Models.AnswerResponse resp = canon.agent().query(
                new Models.AnswerRequest(question, 8, null, null),
                "example-001");
        return resp.answer();
    }
}
```

Without Spring Boot, use the builder:

```java
CanonClient canon = CanonClient.builder()
        .baseUrl("https://canon.example.com")
        .tenantId("acme")
        .workspaceId("ws-prod")
        .agentToken("canon_xxxxx")
        .build();

Models.AnswerResponse resp = canon.agent().query(
        new Models.AnswerRequest("What's our retention policy?", 8, null, null),
        "example-001");
System.out.println(resp.answer());
```

See [QUICKSTART.md](QUICKSTART.md) for the longer tour, including a
multi-tenant pattern (one `CanonClient` per tenant via
`flycanon.auto-configure=false` and an explicit `@Bean`).

## Constructor reference

### `CanonClient.Builder` (also `ReactiveCanonClient.Builder`)

**Required**

| Setter | Type | Description |
|--------|------|-------------|
| `baseUrl(String)` | required | Service base URL. |

**Optional wire-contract headers**

| Setter | Header sent | Notes |
|--------|-------------|-------|
| `tenantId(String)` | `X-Tenant-Id` | Required by the service on every route. |
| `workspaceId(String)` | `X-Workspace-Id` | Required by the service on every route. |
| `correlationId(String)` | `X-Correlation-Id` | Usually rotated per call by the caller. |
| `agentToken(String)` | `X-Agent-Token` | Only meaningful on `/api/v1/agent/*` routes. |

The SDK only emits a header when you set a non-blank value -- it does
not pre-validate; the service rejects missing tenant / workspace
at the boundary as a typed exception.

**Advanced**

| Setter | Description |
|--------|-------------|
| `apiKey(String)` | Bearer token sent as `Authorization: Bearer ...`. |
| `timeout(Duration)` | Read/connect timeout (default `60s`). |
| `restClientBuilder(...)` / `webClientBuilder(...)` | Plug filters, observation hooks, or a custom connector. |
| `objectMapper(ObjectMapper)` | Override the default Jackson mapper. |

### Spring Boot properties (`flycanon.*`)

| Property | Default | Notes |
|----------|---------|-------|
| `flycanon.base-url` | _(required)_ | Root URL of the service. |
| `flycanon.api-key` | _(empty)_ | Sent as `Authorization: Bearer ...`. |
| `flycanon.tenant-id` | _(empty)_ | Sent as `X-Tenant-Id`. |
| `flycanon.workspace-id` | _(empty)_ | Sent as `X-Workspace-Id`. |
| `flycanon.correlation-id` | _(empty)_ | Sent as `X-Correlation-Id`. |
| `flycanon.agent-token` | _(empty)_ | Sent as `X-Agent-Token`. |
| `flycanon.timeout` | `60s` | ISO-8601 duration. Applies to both clients. |
| `flycanon.auto-configure` | `true` | Wire the blocking `CanonClient` bean. |
| `flycanon.reactive-auto-configure` | `false` | Wire the `ReactiveCanonClient` bean. Requires `spring-webflux`. |

## Method catalog

### Workspace CRUD (`/api/v1/workspaces`, user-tier)

| Method | Description |
|--------|-------------|
| `createWorkspace(WorkspaceCreate spec) -> WorkspaceSpec` | Create a workspace under the caller's tenant. |
| `listWorkspaces() -> List<WorkspaceSummary>` | List every workspace owned by the caller's tenant (`created_at DESC`). |
| `getWorkspace(String workspaceId) -> WorkspaceSpec` | Fetch a single workspace by id. |
| `updateWorkspace(String workspaceId, WorkspaceUpdate patch) -> WorkspaceSpec` | Sparse PATCH; only present fields are applied. |
| `closeWorkspace(String workspaceId) -> WorkspaceSpec` | Terminal lifecycle transition (`status=closed`). Idempotent. |

On `ReactiveCanonClient` the same five methods return `Mono<...>` /
`Mono<List<...>>`.

### Agent token CRUD (`/api/v1/agent-tokens`, user-tier)

| Method | Description |
|--------|-------------|
| `mintAgentToken(AgentTokenMintRequest req) -> AgentTokenCreated` | Mint a new token. Raw `token` is returned **once**. |
| `listAgentTokens() -> List<AgentTokenSummary>` | List tokens for the caller's tenant. Secret omitted. |
| `revokeAgentToken(String tokenId) -> void` | Revoke a token. Idempotent. |

Agent-tier callers (authenticated with `X-Agent-Token`) are refused
on the mint path with `403 agent_cannot_mint`.

### Agent surface (`/api/v1/agent/*`, agent-tier, exposed via `client.agent()`)

Every POST mandates a non-empty `idempotencyKey`. The SDK rejects
empty / whitespace values with `IllegalArgumentException` locally,
before the request goes out.

| Blocking method | Description |
|-----------------|-------------|
| `agent().ingestSource(spec, idempotencyKey) -> SourceRecord` | Submit a source for intake. Scope: `agent.sources:ingest`. |
| `agent().getSource(sourceId) -> SourceRecord` | Read a source. Scope: `agent.sources:read`. |
| `agent().query(request, idempotencyKey) -> AnswerResponse` | Grounded RAG answer with citations. Scope: `agent.query:run`. |
| `agent().queryStreamUrl(idempotencyKey) -> String` | Validates the key + returns the SSE URL; stream from the reactive client or your own SSE consumer. |
| `agent().search(request, idempotencyKey) -> SearchResponse` | Hybrid retrieval, no LLM. Scope: `agent.query:run`. |
| `agent().getKnowledge(itemId) -> KnowledgeItem` | Fetch a knowledge item. Scope: `agent.knowledge:read`. |
| `agent().getProvenance(itemId) -> Provenance` | Citation graph for `(itemId, current version)`. Scope: `agent.knowledge:read`. |
| `agent().proposeCandidates(request, idempotencyKey) -> List<CandidateRecord>` | Propose candidates from a source. Scope: `agent.candidates:propose`. |

The reactive client mirrors the same surface; `queryStream(...)`
replaces `queryStreamUrl(...)` and returns `Flux<StreamFrame>`
directly.

### User-tier surface (selected highlights)

```java
// Bulk + async + replace intake
Models.BulkSourcesResponse bulk = canon.submitSourcesBulk(List.of(p1, p2));
Models.IngestJob job = canon.submitSourceAsync(payload);
String sseUrl = canon.jobStreamUrl(job.id(), 0);
Models.SourceRecord updated = canon.replaceSource(sourceId, newPayload);

// Knowledge graph + diff
Models.KnowledgeDiff diff = canon.getDiff(itemId, 1, 2);
Models.RelationsList rels = canon.listRelations(itemId);
canon.addRelation(itemId, new Models.CreateRelationRequest(
        otherId, "depends_on", null, null, null));
Models.KnowledgeGraph graph = canon.getGraph(Map.of("domain", "compliance"));
String mermaid = canon.getGraphMermaid(Map.of("domain", "compliance"));

// Conversations
Models.Conversation conv = canon.createConversation(
        new Models.CreateConversationRequest("Onboarding", null));
Models.ConversationTurn turn = canon.addTurn(conv.id(),
        new Models.CreateConversationTurnRequest(
                "What about scope?", null, null, null));
Models.SuggestionsResponse suggestions = canon.suggestQuestions(conv.id());

// Quality scans
Models.StaleReport stale = canon.scanStale();
Models.ConflictScanResponse conflicts = canon.detectConflicts(
        new Models.ConflictScanRequest("compliance", 0.85, 50, null));

// Billing + corpus inventory
Models.BillingSummary summary = canon.billingSummary(Map.of());
Models.TopConsumersReport top = canon.billingTop(Map.of("dimension", "model"));
Models.LatencyReport lat = canon.billingLatency(Map.of("group_by", "model"));
Models.CorpusStats snapshot = canon.stats();
```

Every return is a Jackson-deserialised record (`@JsonIgnoreProperties`
keeps the SDK forward-compatible with new fields the service ships on
a minor version).

## Reactive variant -- `ReactiveCanonClient`

Same method names as `CanonClient`, but every unary call returns
`Mono<T>` and every SSE-backed call returns `Flux<StreamFrame>`. Use
from Spring WebFlux applications, or from anywhere you'd rather
compose retrievals into a reactive chain.

Streaming is the killer feature this variant unlocks:

```java
// Live job progress.
canon.streamJob(jobId, 0)
     .doOnNext(frame -> log.info("stage={} data={}", frame.event(), frame.data()))
     .takeUntil(f -> "completed".equals(f.event()) || "failed".equals(f.event()))
     .blockLast();  // or compose into a larger pipeline

// Token-by-token user-tier answer.
canon.streamAnswer(new Models.AnswerRequest(
        "Summarise the scope section.", 8, null, null))
     .map(frame -> (String) frame.data().getOrDefault("text", ""))
     .doOnNext(System.out::print)
     .blockLast();

// Agent-tier streamed answer (hit + final frames).
canon.agent()
     .queryStream(new Models.AnswerRequest(
             "What's our retention policy?", 8, null, null),
             "stream-2026-05-22-001")
     .doOnNext(frame -> log.info("event={}", frame.event()))
     .blockLast();
```

Manual construction stays available via
`ReactiveCanonClient.builder()`. Pass your own `WebClient.Builder`
via `.webClientBuilder(...)` to plug filters, retries, observation
hooks, or a custom `ClientHttpConnector`.

## Idempotency-Key

The five agent-tier POSTs (`ingestSource`, `query`, `queryStream`,
`search`, `proposeCandidates`) **require** the `Idempotency-Key`
header on the wire. The SDK exposes that as a mandatory positional
argument:

```java
canon.agent().ingestSource(spec, "ingest-2026-05-22-001");
```

**Contract.** The first call with a given key persists the response.
Subsequent calls with the same key + same body return the **same**
response body byte-for-byte (server-side dedup). Pass a different
body with the same key and the service rejects the retry as a
collision.

**Local validation.** The SDK rejects empty / whitespace keys with
`IllegalArgumentException` before the request goes out so you don't
pay a round-trip to discover you forgot to pass one.

## Error catalog

Every non-2xx response is parsed as RFC 7807 ProblemDetails and
surfaced as a typed `CanonAPIException` subclass keyed by the
service's stable `code` field. All subclasses inherit from
`CanonAPIException`, so generic `catch (CanonAPIException)` keeps
working; catch the subclass to handle a specific failure mode.

| Exception | HTTP | `code` (RFC 7807) |
|-----------|------|--------------------|
| `MissingIdempotencyKey` | 400 | `missing_idempotency_key` |
| `ValidationError` | 400 | `invalid_request` (carries `errors()`) |
| `MissingAgentToken` | 401 | `missing_agent_token` |
| `InvalidAgentToken` | 403 | `invalid_agent_token` |
| `AgentTokenExpired` | 403 | `agent_token_expired` |
| `AgentWorkspaceNotInAllowlist` | 403 | `agent_workspace_not_in_allowlist` |
| `AgentScopeDenied` | 403 | `agent_scope_denied` |
| `AgentCannotMint` | 403 | `agent_cannot_mint` |
| `CanonAPIException` | any | catch-all for unrecognised codes |

```java
try {
    canon.agent().ingestSource(spec, "k-001");
} catch (MissingIdempotencyKey ex) {
    // ...
} catch (AgentScopeDenied ex) {
    // ...
} catch (CanonAPIException ex) {                 // catch-all
    log.error("flycanon error: {} {} -- {}",
            ex.status(), ex.code(), ex.title());
}
```

`ValidationError` (and any other typed exception that carries an
`errors` array) exposes per-field `Models.FieldError(code, path,
message)` records via `ex.errors()` parsed from the
`ProblemDetail.errors` payload.

With the reactive client, the error is signalled through the
`Mono` / `Flux` -- compose with `onErrorResume` / `onErrorReturn`:

```java
canon.agent().getKnowledge("missing-id")
     .onErrorResume(CanonAPIException.class, ex ->
         "knowledge_item_not_found".equals(ex.code())
             ? Mono.empty()
             : Mono.error(ex));
```

## Workspace lifecycle events

flycanon emits three event types on the `canon.workspaces.v1` topic.
The SDK ships the typed records so consumers can deserialise event
payloads directly; the SDK itself does not subscribe -- wire the
topic with your own EDA stack (Kafka / RabbitMQ / etc.) and use the
records to parse payload bodies.

```java
import com.firefly.flycanon.sdk.model.Models;

assert "canon.workspaces.v1".equals(Models.CANON_WORKSPACES_TOPIC);

// Inside your EDA consumer:
void handle(String json) throws Exception {
    JsonNode node = mapper.readTree(json);
    String eventType = node.get("event_type").asText();
    switch (eventType) {
        case "workspace.created" -> {
            Models.WorkspaceCreated evt =
                mapper.treeToValue(node, Models.WorkspaceCreated.class);
            // ...
        }
        case "workspace.updated" -> {
            Models.WorkspaceUpdated evt =
                mapper.treeToValue(node, Models.WorkspaceUpdated.class);
            // ...
        }
        case "workspace.deleted" -> {
            Models.WorkspaceDeleted evt =
                mapper.treeToValue(node, Models.WorkspaceDeleted.class);
            // ...
        }
        default -> { /* ignore */ }
    }
}
```

Every event carries `tenantId`, `workspaceId`, `occurredAt`,
`eventType`, plus per-event payload fields. `workspace.deleted`
corresponds to `POST /api/v1/workspaces/{id}:close` -- flycanon's
terminal lifecycle transition (the row is preserved for audit; the
canonical event name matches the lifecycle vocabulary other services
use).

## Versioning

The SDK pins its version to the service's CalVer (`YY.MM.PP`), so
`flycanon-sdk@26.5.7` is the matching client for service version
`26.5.x`. Upgrade the SDK in lockstep with the service. Both
clients (`CanonClient`, `ReactiveCanonClient`) ship from the same
artifact -- no separate dependency to manage.

## Additional resources

- [CHANGELOG.md](CHANGELOG.md) -- wire-contract history per release.
- `openapi.json` -- full route list (served by the running service
  at `/openapi.json`).
- Consumers guide -- TBD, will live at
  [`flycanon/docs/consumers.md`](../../docs/consumers.md).

## License

Apache-2.0 -- see [LICENSE](LICENSE).
