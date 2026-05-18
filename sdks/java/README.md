<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Java SDK** &nbsp;·&nbsp; Spring Boot 3.5.9 &nbsp;·&nbsp; Java 25

</div>

---

Spring-Boot-native Java client for the
[flycanon](https://github.com/firefly-operationOS/flycanon) Operational
Knowledge Repository service.

* **Java 25** (LTS) / **Spring Boot 3.5.9** / **Spring Framework 6.2**.
* Two clients side by side:
  * **`CanonClient`** -- synchronous, blocking; built on Spring's
    `RestClient` (the new default that replaces `RestTemplate` in
    Spring 6.1+) and Jackson. Use from plain Spring MVC / Servlet
    stacks.
  * **`ReactiveCanonClient`** -- non-blocking; built on `WebClient`
    + Reactor Netty. Returns `Mono<T>` for unary methods and
    `Flux<StreamFrame>` for the SSE streams (job progress, answer
    streaming). Use from Spring WebFlux or any reactive chain.
* Ships an `@AutoConfiguration` for each variant; the blocking bean
  is wired by default, the reactive bean is opt-in via
  `flycanon.reactive-auto-configure=true`.
* `groupId = com.firefly`. Apache-2.0.

## Install

```xml
<dependency>
  <groupId>com.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.5</version>
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

## Configuration

The SDK binds `flycanon.*` properties via
`@ConfigurationProperties`:

| Property                          | Default            | Notes                                                              |
|-----------------------------------|--------------------|--------------------------------------------------------------------|
| `flycanon.base-url`               | _(required)_       | Root URL of the service.                                           |
| `flycanon.api-key`                | _(empty)_          | Sent as `Authorization: Bearer ...`.                               |
| `flycanon.timeout`                | `60s`              | Read/connect timeout. ISO-8601 duration. Applies to both clients.  |
| `flycanon.auto-configure`         | `true`             | Wire the blocking `CanonClient` bean. Set `false` to skip.         |
| `flycanon.reactive-auto-configure`| `false`            | Wire the `ReactiveCanonClient` bean. Requires `spring-webflux`.    |

`application.yml` example:

```yaml
flycanon:
  base-url: https://canon.internal.example.com
  api-key: ${FLYCANON_API_KEY}
  timeout: 30s
```

## Usage

With Spring Boot, just inject the bean:

```java
import com.firefly.flycanon.sdk.CanonClient;
import com.firefly.flycanon.sdk.model.Models.AnswerResponse;
import com.firefly.flycanon.sdk.model.Models.SourceRecord;
import com.firefly.flycanon.sdk.model.Models.SubmitSourceJsonPayload;
import org.springframework.stereotype.Service;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.Map;

@Service
public class CopilotService {

    private final CanonClient canon;

    public CopilotService(CanonClient canon) {
        this.canon = canon;
    }

    public AnswerResponse ingestAndAsk(Path file, String question) throws Exception {
        byte[] bytes = Files.readAllBytes(file);
        SourceRecord source = canon.submitSource(new SubmitSourceJsonPayload(
                "unknown",
                null,
                Map.of("title", file.getFileName().toString(), "domain", "process"),
                Base64.getEncoder().encodeToString(bytes),
                file.getFileName().toString(),
                "application/octet-stream"));

        return canon.answer(question);
    }
}
```

Without Spring Boot (plain Java 25 program, or a different
framework), use the builder:

```java
CanonClient client = CanonClient.builder()
        .baseUrl("http://localhost:8500")
        .apiKey(System.getenv("FLYCANON_API_KEY"))
        .build();
```

See [QUICKSTART.md](QUICKSTART.md) for the full tour, including a
multi-tenant pattern (one `CanonClient` per tenant via
`flycanon.auto-configure=false` and an explicit `@Bean`).

## What the client covers

Every public flycanon endpoint has a typed method on `CanonClient`.
Highlights of the Tier 1 / Tier 2 surfaces:

```java
// Bulk + async + replace intake
Models.BulkSourcesResponse bulk = canon.submitSourcesBulk(List.of(p1, p2));
Models.IngestJob job = canon.submitSourceAsync(payload);
String sseUrl = canon.jobStreamUrl(job.id(), 0);
// (Use Spring's WebClient or any HTTP/2 streaming client against
// sseUrl -- RestClient is blocking and unsuitable for live SSE.)
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

```java
import com.firefly.flycanon.sdk.ReactiveCanonClient;
import com.firefly.flycanon.sdk.model.Models;

@Service
public class CopilotService {

    private final ReactiveCanonClient canon;

    public CopilotService(ReactiveCanonClient canon) {
        this.canon = canon;
    }

    public Mono<Models.AnswerResponse> ask(String question) {
        return canon.answer(question);
    }
}
```

Streaming over Server-Sent Events is the killer feature this variant
unlocks -- the blocking client can only hand back the SSE URL:

```java
// Live job progress.
canon.streamJob(jobId, 0)
     .doOnNext(frame -> log.info("stage={} data={}", frame.event(), frame.data()))
     .takeUntil(f -> "completed".equals(f.event()) || "failed".equals(f.event()))
     .blockLast();  // or compose into a larger pipeline

// Token-by-token answer.
canon.streamAnswer(new Models.AnswerRequest(
        "Summarise the scope section.", 8, null, null))
     .map(frame -> (String) frame.data().getOrDefault("text", ""))
     .doOnNext(System.out::print)
     .blockLast();
```

Manual construction stays available via
`ReactiveCanonClient.builder()`:

```java
ReactiveCanonClient canon = ReactiveCanonClient.builder()
        .baseUrl("http://localhost:8500")
        .apiKey(System.getenv("FLYCANON_API_KEY"))
        .timeout(Duration.ofSeconds(30))
        .build();
```

Pass your own `WebClient.Builder` via `.webClientBuilder(...)` to
plug filters, retries, observation hooks, or a custom
`ClientHttpConnector` -- the SDK builds on top of whatever
configuration you supply.

## Error handling

Every non-2xx response is parsed as RFC 7807 ProblemDetails and
surfaced as `CanonAPIException`. The exception carries the service's
stable `code` plus the raw extensions map for branching.

With the blocking client:

```java
try {
    canon.getKnowledge("missing-id");
} catch (CanonAPIException ex) {
    if ("knowledge_item_not_found".equals(ex.code())) {
        // graceful 404 handling
    } else {
        throw ex;
    }
}
```

With the reactive client, the error is signalled through the
`Mono` / `Flux` -- compose with `onErrorResume` / `onErrorReturn`:

```java
canon.getKnowledge("missing-id")
     .onErrorResume(CanonAPIException.class, ex ->
         "knowledge_item_not_found".equals(ex.code())
             ? Mono.empty()
             : Mono.error(ex));
```

## Versioning

The SDK pins its version to the service's CalVer (`YY.MM.PP`), so
`flycanon-sdk@26.5.5` is the matching client for service version
`26.5.5`. Upgrade the SDK in lockstep with the service. Both
clients (`CanonClient`, `ReactiveCanonClient`) ship from the same
artifact -- no separate dependency to manage.

## License

Apache-2.0 -- see [LICENSE](LICENSE).
