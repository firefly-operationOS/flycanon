# flycanon-sdk Java quickstart

Five-minute tour of the Java client. Assumes the flycanon service is
running on `http://localhost:8500`.

## 1. Add the dependency

```xml
<dependency>
  <groupId>io.firefly</groupId>
  <artifactId>flycanon-sdk</artifactId>
  <version>26.5.1</version>
</dependency>
```

## 2. Build the client

```java
CanonClient client = CanonClient.builder()
        .baseUrl("http://localhost:8500")
        .apiKey(System.getenv("FLYCANON_API_KEY")) // optional
        .build();
```

## 3. Ingest a source

```java
byte[] bytes = Files.readAllBytes(Path.of("sample.docx"));
SubmitSourceJsonPayload payload = new SubmitSourceJsonPayload(
        "unknown",
        null,
        Map.of("title", "Sample", "domain", "process"),
        Base64.getEncoder().encodeToString(bytes),
        "sample.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
SourceRecord source = client.submitSource(payload);
System.out.println(source.id() + " ingested with " + source.nChunks() + " chunks");
```

## 4. Search

```java
SearchResponse hits = client.search("scope", 5);
hits.hits().forEach(h -> System.out.println(h.score() + "  " + h.content()));
```

## 5. Ask

```java
AnswerResponse answer = client.answer("Summarise the scope section in three sentences.");
System.out.println(answer.answer());
```

## Error handling

```java
try {
    client.getKnowledge("nope");
} catch (CanonAPIException ex) {
    if ("knowledge_item_not_found".equals(ex.code())) {
        // graceful 404 handling
    } else {
        throw ex;
    }
}
```
