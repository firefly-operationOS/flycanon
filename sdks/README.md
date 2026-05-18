# flycanon SDKs

Official client libraries for the flycanon service. Both are released
under the Apache License 2.0 (the service itself is proprietary --
see the top-level [LICENSE](../LICENSE)).

| SDK                          | Status | Notes |
|------------------------------|--------|-------|
| [Python](python/README.md)   | Beta   | Async-first, httpx + Pydantic. Python >= 3.11. |
| [Java](java/README.md)       | Beta   | Spring Boot 3.5.9 + Spring `RestClient` + Jackson. Java 25 (LTS). `groupId = com.firefly`. Ships an `@AutoConfiguration`. |

Each SDK pins its version to the service's CalVer (`YY.MM.PP`), so
`flycanon-sdk@26.5.1` is the matching client for service version
`26.5.1`.

Need a TypeScript / Go / C# SDK? File an issue on the
[flycanon repo](https://github.com/firefly-operationOS/flycanon/issues).
