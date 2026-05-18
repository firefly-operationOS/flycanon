<div align="center">

<img src="../../docs/assets/logo.png" alt="flycanon" width="380" />

### **Java SDK** &nbsp;·&nbsp; Spring Boot 3.5.9 &nbsp;·&nbsp; Java 25

</div>

---

Spring-Boot-native Java client for the
[flycanon](https://github.com/firefly-operationOS/flycanon) Operational
Knowledge Repository service.

* **Java 25** (LTS) / **Spring Boot 3.5.9** / **Spring Framework 6.2**.
* Built on `RestClient` (the synchronous, blocking HTTP client that
  replaces `RestTemplate` as the default in Spring 6.1+) and Jackson.
* Ships an `@AutoConfiguration` so a `CanonClient` bean is wired
  straight from `flycanon.*` properties.
* `groupId = com.firefly`. Apache-2.0.

## Install

```xml
<dependency>
  <groupId>com.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.1</version>
</dependency>
```

That's the whole installation. The SDK declares `spring-boot-starter`
and `spring-web` as compile dependencies, so a Spring Boot 3.5.x
application picks up everything it needs transitively.

## Configuration

The SDK binds `flycanon.*` properties via
`@ConfigurationProperties`:

| Property                 | Default            | Notes                                       |
|--------------------------|--------------------|---------------------------------------------|
| `flycanon.base-url`      | _(required)_       | Root URL of the service.                    |
| `flycanon.api-key`       | _(empty)_          | Sent as `Authorization: Bearer ...`.        |
| `flycanon.timeout`       | `60s`              | Read/connect timeout. ISO-8601 duration.    |
| `flycanon.auto-configure`| `true`             | Set `false` to skip the auto-wired bean.    |

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

## Error handling

Every non-2xx response is parsed as RFC 7807 ProblemDetails and
thrown as `CanonAPIException`. The exception carries the service's
stable `code` plus the raw extensions map for branching:

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

## Versioning

The SDK pins its version to the service's CalVer (`YY.MM.PP`), so
`flycanon-sdk@26.5.1` is the matching client for service version
`26.5.1`. Upgrade the SDK in lockstep with the service.

## License

Apache-2.0 -- see [LICENSE](LICENSE).
