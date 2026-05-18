# flycanon docs

The user-facing manual for the flycanon Operational Knowledge
Repository service.

| Doc | What it covers |
|-----|----------------|
| [architecture.md](architecture.md) | Data model, layers, services, dependency arrows. |
| [pipeline.md](pipeline.md)         | Source intake -> retrieval -> answer end-to-end, with the agentic primitives flycanon composes. |
| [api-reference.md](api-reference.md) | Every public REST endpoint with shape + status codes. |
| [payload-reference.md](payload-reference.md) | The wire payloads (request / response + ProblemDetails). |
| [glossary.md](glossary.md)         | Terms the public API uses (canonical knowledge, candidate, supersession, provenance, ...). |
| [eda-events.md](eda-events.md)     | The events flycanon publishes on `flycanon.ingest`, `flycanon.knowledge`, `flycanon.audit`. |

For a quickstart, see the top-level
[QUICKSTART.md](../QUICKSTART.md); for the SDK tour see the docs
under [sdks/python](../sdks/python/README.md) and
[sdks/java](../sdks/java/README.md).
