# Changelog

All notable changes to **flycanon-sdk** (Python) are documented here.

## [26.5.7] - 2026-05-22

### Added -- 12-plan unification surface (non-breaking, additive)

- **Constructor headers.** `CanonClient(...)` accepts four new
  optional kwargs that are injected on every outbound request:
  - `tenant_id` -> `X-Tenant-Id`
  - `workspace_id` -> `X-Workspace-Id`
  - `correlation_id` -> `X-Correlation-Id`
  - `agent_token` -> `X-Agent-Token`

  Each defaults to `None`; the SDK only adds a header when the
  caller supplies a value. The service rejects missing
  tenant/workspace headers at the boundary -- the SDK does not
  pre-validate, it just forwards what it has.

- **Workspace CRUD** (5 methods, user-tier):
  - `client.create_workspace(spec) -> WorkspaceSpec`
  - `client.list_workspaces() -> list[WorkspaceSummary]`
  - `client.get_workspace(workspace_id) -> WorkspaceSpec`
  - `client.update_workspace(workspace_id, patch) -> WorkspaceSpec`
  - `client.close_workspace(workspace_id) -> WorkspaceSpec`

  Backed by new DTOs `WorkspaceCreate`, `WorkspaceUpdate`,
  `WorkspaceSpec`, `WorkspaceSummary`.

- **Agent token CRUD** (3 methods, user-tier):
  - `client.mint_agent_token(request) -> AgentTokenCreated`
    (the raw `token` is returned ONCE)
  - `client.list_agent_tokens() -> list[AgentTokenSummary]`
  - `client.revoke_agent_token(token_id) -> None`

  Backed by new DTOs `AgentTokenMintRequest`,
  `AgentTokenSummary`, `AgentTokenCreated`.

- **Agent surface** (8 methods exposed via `client.agent`):
  - `client.agent.ingest_source(spec, *, idempotency_key)`
  - `client.agent.get_source(source_id)`
  - `client.agent.query(request, *, idempotency_key)`
  - `client.agent.query_stream(request, *, idempotency_key)`
  - `client.agent.search(request, *, idempotency_key)`
  - `client.agent.get_knowledge(item_id)`
  - `client.agent.get_provenance(item_id, *, version=None)`
  - `client.agent.propose_candidates(request, *, idempotency_key)`

  All POSTs mandate a non-empty `idempotency_key` argument. The
  SDK rejects empty / whitespace values locally with `ValueError`
  before the round-trip; the service enforces the same shape via
  `400 missing_idempotency_key`.

- **Workspace lifecycle event DTOs** (consumers of
  `canon.workspaces.v1`):
  - `WorkspaceCreated` (event_type=`workspace.created`)
  - `WorkspaceUpdated` (event_type=`workspace.updated`)
  - `WorkspaceDeleted` (event_type=`workspace.deleted`)

  Frozen Pydantic models with the canonical `tenant_id`,
  `workspace_id`, `occurred_at` plus per-event payload fields.

- **`CANON_WORKSPACES_TOPIC`** constant (`"canon.workspaces.v1"`)
  exposed at the package top level for consumers wiring topic
  subscriptions.

- **Typed exception classes** keyed by the service's stable
  RFC 7807 `code` field. All inherit from `CanonAPIError` so
  generic `except CanonAPIError` keeps working:
  - `MissingIdempotencyKey` (400 `missing_idempotency_key`)
  - `MissingAgentToken` (401 `missing_agent_token`)
  - `InvalidAgentToken` (403 `invalid_agent_token`)
  - `AgentTokenExpired` (403 `agent_token_expired`)
  - `AgentWorkspaceNotInAllowlist` (403
    `agent_workspace_not_in_allowlist`)
  - `AgentScopeDenied` (403 `agent_scope_denied`)
  - `AgentCannotMint` (403 `agent_cannot_mint`)
  - `ValidationError` (400 `invalid_request`)

- **Structured field-level error parsing.** The
  `ProblemDetail.errors` array (per-field validation triples) is
  parsed into `FieldError(code, path, message)` instances and
  surfaced on the exception's `.errors` attribute. Most useful on
  `ValidationError` but available on every typed exception that
  carries the array.

- **Per-call header overrides.** Each handler accepts an internal
  `headers=` kwarg on `_request(...)`, used to attach
  `Idempotency-Key` per agent-tier POST without polluting the
  client's default headers.

### Changed

- **`/api/v1/jobs` -> `/api/v1/ingest-jobs`.** The SDK helpers
  `client.get_job`, `client.cancel_job`, and `client.stream_job`
  now hit `/api/v1/ingest-jobs/...` to match the renamed service
  routes.

- **`_raise_for_problem`** now resolves the exception class via
  the new code registry, so older callers with
  `except CanonAPIError` still receive a `CanonAPIError`
  (subclass), but new callers can catch the specific subclass.

- **`__version__`** bumped to `26.5.7` (was `26.5.5` -- there was
  a pre-existing drift between `pyproject.toml` (26.5.6) and the
  package `__version__`; this release realigns both).

- **`User-Agent`** advertises `flycanon-sdk-python/26.5.7`.

### Internal

- Ruff config gains a per-file ignore for `N818` on `_errors.py`
  -- the new exception class names (e.g. `MissingAgentToken`)
  intentionally mirror the service's RFC 7807 `code` field
  rather than carry an `Error` suffix.

## [26.5.6] - 2026-05-19

- Pre-26.5.7 baseline. See git history for details.
