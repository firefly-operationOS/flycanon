# Contributing to flycanon

Thanks for working on flycanon. This document captures the
non-obvious workflow rules that the PR gate enforces, so the first
push doesn't bounce on something easy to fix.

For the broader contribution model (commit conventions, branch
naming, release process) see the top-level Firefly OperationOS
contributor guide.

## OpenAPI snapshot gate

Every REST contract change must land alongside a regenerated
`openapi.json` at the repo root. CI runs
`tests/unit/test_openapi_snapshot.py`, which boots the live FastAPI
app, generates the spec through pyfly's introspector (the same path
the production lifespan takes), and diffs it against the committed
snapshot. Drift fails the unit job with a hard error.

The intent is to prevent silent SDK drift: the published Python and
Java SDKs under `sdks/` regenerate from this exact spec, so an
unreviewed contract change ships to clients with no audit trail.

### When to regenerate

Any of these changes flip the snapshot:

- adding, renaming, removing or re-pathing a route,
- changing a request body, query parameter, path parameter,
  response model, status code, or media type,
- changing an `operationId`, `summary`, `description`, or tag on a
  controller method,
- editing a shared schema (DTO, enum, response envelope) under
  `src/flycanon/interfaces/` or anywhere a controller references,
- adding or changing an exception response (RFC 7807 problem
  detail mappings show up in `responses`),
- editing a controller method docstring (the docstring becomes the
  OpenAPI operation description).

### Regenerate

From the repo root:

```bash
uv run python scripts/update_openapi_snapshot.py
```

The script seeds safe in-memory defaults (`FLYCANON_DATABASE_URL`,
`FLYCANON_EDA_ADAPTER=memory`, dummy LLM keys) so it runs on a
clean checkout with no Postgres, Redis or provider credentials
required. It writes `openapi.json` with `sort_keys=True` and
`indent=2` so the PR diff stays readable.

Commit the resulting `openapi.json` change in the **same PR** as
the route change.

### Verify locally

```bash
uv run pytest tests/unit/test_openapi_snapshot.py -v
```

A green run means CI will accept the snapshot.

### What if the test fails?

The assertion message includes the regeneration command. Re-run it,
inspect the diff (`git diff openapi.json`) to make sure the change
is what you intended, then `git add openapi.json` and commit.
