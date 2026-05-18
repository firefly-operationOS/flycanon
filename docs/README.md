<p align="center">
  <img src="assets/logo.png" alt="flycanon" width="380" />
</p>

# flycanon docs

The user-facing manual for the flycanon Operational Knowledge
Repository service.

| Doc | What it covers |
|-----|----------------|
| [architecture.md](architecture.md) | Data model, layers, services, the binary-normaliser routing matrix, the pluggable retrieval backend matrix, dependency arrows. |
| [pipeline.md](pipeline.md)         | Source intake -> retrieval -> answer end-to-end, with the agentic primitives flycanon composes. |
| [api-reference.md](api-reference.md) | Every public REST endpoint with shape + status codes. |
| [payload-reference.md](payload-reference.md) | The wire payloads (request / response + RFC 7807 ProblemDetails). |
| [glossary.md](glossary.md)         | Terms the public API uses (canonical knowledge, candidate, supersession, provenance, ...). |
| [eda-events.md](eda-events.md)     | The events flycanon publishes on `flycanon.ingest`, `flycanon.knowledge`, `flycanon.audit`. |

For a quickstart, see the top-level
[QUICKSTART.md](../QUICKSTART.md); for the SDK tours see
[sdks/python](../sdks/python/README.md) (async-first Python) and
[sdks/java](../sdks/java/README.md) (Spring Boot 3.5.9 + Java 25 +
`com.firefly`).

The OpenAPI document is served live by the running service at
`/openapi.json`, with Swagger UI at `/docs` and Redoc at `/redoc`.
