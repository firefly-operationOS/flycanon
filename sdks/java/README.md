# flycanon-sdk (Java)

Java client for the
[flycanon](https://github.com/firefly-operationOS/flycanon) Operational
Knowledge Repository service.

* Java 21+, `java.net.http.HttpClient` under the hood -- no Spring,
  no reactor, no transitive HTTP-client zoo.
* Jackson for JSON.
* Apache-2.0.

## Install

```xml
<dependency>
  <groupId>io.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.1</version>
</dependency>
```

## Usage

```java
import io.firefly.flycanon.sdk.CanonClient;
import io.firefly.flycanon.sdk.model.AnswerResponse;
import io.firefly.flycanon.sdk.model.SourceRecord;
import io.firefly.flycanon.sdk.model.SubmitSourceJsonPayload;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.Map;

try (CanonClient client = CanonClient.builder()
        .baseUrl("http://localhost:8500")
        .build()) {

    byte[] bytes = Files.readAllBytes(Path.of("sample.docx"));
    SourceRecord source = client.submitSource(new SubmitSourceJsonPayload(
            "unknown",
            null,
            Map.of("title", "Sample", "domain", "process"),
            Base64.getEncoder().encodeToString(bytes),
            "sample.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"));

    AnswerResponse answer = client.answer("Summarise the scope section.");
    System.out.println(answer.answer());
    answer.citations().forEach(c -> System.out.println(" - " + c.sourceId() + " " + c.score()));
}
```

See [QUICKSTART.md](QUICKSTART.md) for the full tour.

## Error handling

The client throws `CanonAPIException` on any non-2xx response. The
exception carries the service's stable `code` plus the raw
ProblemDetails payload for branching:

```java
try {
    client.getKnowledge("missing-id");
} catch (CanonAPIException ex) {
    if ("knowledge_item_not_found".equals(ex.code())) {
        // ...
    }
}
```

## License

Apache-2.0 -- see [LICENSE](LICENSE).
