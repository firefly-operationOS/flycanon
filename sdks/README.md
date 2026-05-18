<div align="center">

<img src="../docs/assets/logo.png" alt="flycanon" width="380" />

### **SDKs**

</div>

---

Official client libraries for the flycanon service. Both are released
under the Apache License 2.0 (the service itself is proprietary —
see the top-level [LICENSE](../LICENSE)).

| SDK | Highlights | Read it when… |
|-----|------------|---------------|
| [**Python**](python/README.md) | Async-first, `httpx` + Pydantic. Python ≥ 3.11. | You're integrating from Python and want non-blocking IO with typed request / response models. |
| [**Java**](java/README.md)     | **Spring Boot 3.5.9 + Spring `RestClient` + Jackson. Java 25 (LTS). `groupId = com.firefly`.** Ships an `@AutoConfiguration`. | You're integrating from a Spring Boot 3.5.x application — declare the dependency and inject the `CanonClient` bean. |

Each SDK pins its version to the service's CalVer (`YY.MM.PP`), so
`flycanon-sdk@26.5.4` is the matching client for service version
`26.5.4`.

Quickstarts:

- [Python SDK quickstart](python/QUICKSTART.md) — five-minute tour.
- [Java SDK quickstart](java/QUICKSTART.md) — ten-minute tour with
  a multi-tenant pattern and a custom `RestClient.Builder` hook.

Need a TypeScript / Go / C# SDK? File an issue on the
[flycanon repo](https://github.com/firefly-operationOS/flycanon/issues).
