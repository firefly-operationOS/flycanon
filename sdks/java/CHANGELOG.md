# Changelog

All notable changes to **flycanon-sdk** (Java) are documented here.

The SDK uses CalVer (`YY.MM.Patch`). Breaking changes are signalled
in the entry header and the **Breaking** section, not in the version
number (CalVer has no "major" axis to bump).

## [26.5.7] - 2026-05-22

### Added -- 12-plan unification surface (non-breaking, additive)

- **Constructor properties.** `CanonClient.Builder` (and
  `ReactiveCanonClient.Builder`) accept four new optional setters
  that are injected on every outbound request:
  - `tenantId(String)` -> `X-Tenant-Id`
  - `workspaceId(String)` -> `X-Workspace-Id`
  - `correlationId(String)` -> `X-Correlation-Id`
  - `agentToken(String)` -> `X-Agent-Token`

  Each defaults to `null`; the SDK only adds a header when the
  caller supplies a value. The service rejects missing
  tenant/workspace headers at the boundary -- the SDK does not
  pre-validate, it just forwards what it has.

- **`CanonClientProperties`** exposes matching `flycanon.tenant-id`
  / `flycanon.workspace-id` / `flycanon.correlation-id` /
  `flycanon.agent-token` configuration properties wired through
  both autoconfigurations.

- **Workspace CRUD** (5 methods, user-tier):
  - `client.createWorkspace(WorkspaceCreate spec) -> WorkspaceSpec`
  - `client.listWorkspaces() -> List<WorkspaceSummary>`
  - `client.getWorkspace(String workspaceId) -> WorkspaceSpec`
  - `client.updateWorkspace(String workspaceId, WorkspaceUpdate patch) -> WorkspaceSpec`
  - `client.closeWorkspace(String workspaceId) -> WorkspaceSpec`

  Backed by new DTOs `WorkspaceCreate`, `WorkspaceUpdate`,
  `WorkspaceSpec`, `WorkspaceSummary`. Mirrored on
  `ReactiveCanonClient` (returns wrapped in `Mono`).

- **Agent token CRUD** (3 methods, user-tier):
  - `client.mintAgentToken(AgentTokenMintRequest req) -> AgentTokenCreated`
    (the raw `token` is returned ONCE)
  - `client.listAgentTokens() -> List<AgentTokenSummary>`
  - `client.revokeAgentToken(String tokenId) -> void`

  Backed by new DTOs `AgentTokenMintRequest`, `AgentTokenSummary`,
  `AgentTokenCreated`.

- **Agent surface** (8 methods exposed via `client.agent()`):
  - `client.agent().ingestSource(spec, idempotencyKey)`
  - `client.agent().getSource(sourceId)`
  - `client.agent().query(request, idempotencyKey)`
  - `client.agent().queryStreamUrl(idempotencyKey)` (blocking)
  - `client.agent().queryStream(request, idempotencyKey)` (reactive only)
  - `client.agent().search(request, idempotencyKey)`
  - `client.agent().getKnowledge(itemId)`
  - `client.agent().getProvenance(itemId)`
  - `client.agent().proposeCandidates(request, idempotencyKey)`

  All POSTs mandate a non-empty `idempotencyKey` argument. The
  SDK rejects empty / whitespace values locally with
  `IllegalArgumentException` before the round-trip; the service
  enforces the same shape via `400 missing_idempotency_key`.

- **Workspace lifecycle event DTOs** (consumers of
  `canon.workspaces.v1`):
  - `Models.WorkspaceCreated` (event_type=`workspace.created`)
  - `Models.WorkspaceUpdated` (event_type=`workspace.updated`)
  - `Models.WorkspaceDeleted` (event_type=`workspace.deleted`)

  Records with Jackson `@JsonProperty` annotations that mirror the
  Python Pydantic shapes byte-for-byte; default `event_type` is set
  when the field is absent so consumers constructing the record
  programmatically don't have to pass the literal.

- **`Models.CANON_WORKSPACES_TOPIC`** constant (`"canon.workspaces.v1"`)
  exposed at the model class top level for consumers wiring topic
  subscriptions.

- **Typed exception classes** keyed by the service's stable
  RFC 7807 `code` field. All inherit from `CanonAPIException` so
  generic `catch (CanonAPIException)` keeps working:
  - `MissingIdempotencyKey` (400 `missing_idempotency_key`)
  - `MissingAgentToken` (401 `missing_agent_token`)
  - `InvalidAgentToken` (403 `invalid_agent_token`)
  - `AgentTokenExpired` (403 `agent_token_expired`)
  - `AgentWorkspaceNotInAllowlist` (403
    `agent_workspace_not_in_allowlist`)
  - `AgentScopeDenied` (403 `agent_scope_denied`)
  - `AgentCannotMint` (403 `agent_cannot_mint`)
  - `ValidationError` (400 `invalid_request`)

  `CanonAPIException.fromProblemDetail(...)` dispatches by `code`;
  unknown codes fall back to the base `CanonAPIException` so older
  clients talking to newer services keep working.

- **Structured field-level error parsing.** The
  `ProblemDetail.errors` array (per-field validation triples) is
  parsed into `Models.FieldError(code, path, message)` records and
  surfaced on `CanonAPIException#errors()`. Most useful on
  `ValidationError` but available on every typed exception that
  carries the array.

- **Per-call header overrides.** The internal `request(...)` helper
  on both clients accepts an `extraHeaders` map, used to attach
  `Idempotency-Key` per agent-tier POST without polluting the
  client's default headers.

### Changed

- **`/api/v1/jobs` -> `/api/v1/ingest-jobs`.** The SDK helpers
  `client.getJob`, `client.cancelJob`, and `client.jobStreamUrl`
  (plus their reactive equivalents and `streamJob`) now hit
  `/api/v1/ingest-jobs/...` to match the renamed service routes.

- **Internal `request(...)` signature.** Added an `extraHeaders`
  parameter; existing callers pass `null` and behave identically.
  Source-compatible -- no public API surface change.

- **`User-Agent`** advertises `flycanon-sdk-java/26.5.7`.

- **Version constant.** `CanonClient.SDK_VERSION = "26.5.7"`.

### Internal

- WireMock test dependency added (test-scope only); the new tests
  assert outbound headers, exception dispatch by `code`, path
  rename, idempotency-key validation, and event DTO JSON
  round-trip via real HTTP stubs.
- `reactor-test` test dependency added for `StepVerifier`-based
  reactive assertions.

## [26.5.6] - 2026-05-19

- Pre-26.5.7 baseline. See git history for details.
