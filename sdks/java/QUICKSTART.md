<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Java SDK quickstart** &nbsp;·&nbsp; Spring Boot 3.5.9 &nbsp;·&nbsp; Java 25

</div>

---

Ten-minute tour of the Spring Boot Java client. Assumes the flycanon
service is reachable on `http://localhost:8500` and you already have
a Spring Boot 3.5.x application on Java 25.

## 1. Add the dependency

```xml
<dependency>
  <groupId>com.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.1</version>
</dependency>
```

The SDK pulls in `spring-boot-starter` and `spring-web`
transitively, so nothing else needs to change in your `pom.xml`.

## 2. Configure the client

`application.yml`:

```yaml
flycanon:
  base-url: http://localhost:8500
  api-key: ${FLYCANON_API_KEY:}
  timeout: 30s
```

That's all the wiring needed. The auto-configuration registers a
`CanonClient` bean conditional on `flycanon.auto-configure`
defaulting to `true`.

## 3. Inject it

```java
import com.firefly.flycanon.sdk.CanonClient;
import org.springframework.stereotype.Service;

@Service
public class KnowledgeService {

    private final CanonClient canon;

    public KnowledgeService(CanonClient canon) {
        this.canon = canon;
    }
}
```

## 4. Ingest a source

`submitSource` accepts ANY file format -- the service detects the
media type from the magic bytes and routes the payload through the
binary normaliser (Office to Markdown, archives expanded, images
OCR'd, emails decomposed, ...).

```java
import com.firefly.flycanon.sdk.model.Models.SourceRecord;
import com.firefly.flycanon.sdk.model.Models.SubmitSourceJsonPayload;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.Map;

byte[] bytes = Files.readAllBytes(Path.of("sample.docx"));
SubmitSourceJsonPayload payload = new SubmitSourceJsonPayload(
        "unknown",                                    // server detects kind
        null,                                         // no URL -- inline upload
        Map.of("title", "Sample", "domain", "process"),
        Base64.getEncoder().encodeToString(bytes),
        "sample.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document");

SourceRecord source = canon.submitSource(payload);
System.out.println(source.id() + " ingested with " + source.nChunks() + " chunks");
```

## 5. Search

Every hit carries the rich source-side context (filename, title,
kind, section breadcrumb, page) at the top level -- no second
`getSource(...)` round-trip needed to render a citation label.

```java
import com.firefly.flycanon.sdk.model.Models.SearchResponse;

SearchResponse hits = canon.search("scope", 5);
hits.hits().forEach(h -> {
    String label = h.sourceFilename() != null ? h.sourceFilename()
            : (h.sourceTitle() != null ? h.sourceTitle() : h.sourceId());
    String location = h.sectionPath() != null ? h.sectionPath() : "-";
    if (h.page() != null) {
        location = location + " (page " + h.page() + ")";
    }
    System.out.printf("%.4f  [%s] %s -- %s%n",
            h.score(), h.sourceKind(), label, location);
    System.out.println("        " + h.content().substring(0, Math.min(120, h.content().length())));
});
```

## 6. Ask a grounded question

```java
import com.firefly.flycanon.sdk.model.Models.AnswerResponse;

AnswerResponse answer = canon.answer("Summarise the scope section in three sentences.");
System.out.println(answer.answer());
answer.citations().forEach(c -> {
    String label = c.sourceFilename() != null ? c.sourceFilename()
            : (c.sourceTitle() != null ? c.sourceTitle() : c.sourceId());
    System.out.printf(" - %s (%s) -- section: %s -- score: %.3f%n",
            label, c.sourceKind(),
            c.sectionPath() != null ? c.sectionPath() : "-",
            c.score());
});
```

## 7. Error handling

Every non-2xx response surfaces as `CanonAPIException` with the
service's stable `code` and the RFC 7807 extensions map:

```java
import com.firefly.flycanon.sdk.CanonAPIException;

try {
    canon.getKnowledge("nope");
} catch (CanonAPIException ex) {
    if ("knowledge_item_not_found".equals(ex.code())) {
        // 404 -- ignore or report
    } else {
        throw ex;
    }
}
```

## Multi-tenant deployments

For applications that need a different flycanon endpoint per
tenant, disable the auto-wired bean and build clients on demand:

```yaml
flycanon:
  auto-configure: false
```

```java
@Configuration
public class CanonClients {

    @Bean
    public Map<String, CanonClient> canonClientsByTenant(TenantRegistry registry) {
        return registry.tenants().stream().collect(Collectors.toMap(
                Tenant::id,
                t -> CanonClient.builder()
                        .baseUrl(t.canonBaseUrl())
                        .apiKey(t.canonApiKey())
                        .build()));
    }
}
```

## Customising the underlying `RestClient`

For advanced needs (mTLS, custom interceptors, observability), pass
your own `RestClient.Builder`:

```java
CanonClient client = CanonClient.builder()
        .baseUrl("https://canon.internal.example.com")
        .restClientBuilder(myConfiguredBuilder)
        .build();
```

The SDK still applies its default `Accept`, `User-Agent` and bearer
auth headers on top of your builder, so you only need to override
what differs.
