<div align="center">

<img src="assets/logo.png" alt="flycanon" width="380" />

### **Consumers**

</div>

---

> Producer-side view. This document is for platform engineers
> building or auditing a service that consumes flycanon. It explains
> what flycanon offers, how to wire a consumer through the agent
> surface, and the responsibilities the consumer carries on its
> side.

The canonical consumer today is **flyradar** -- it hands off
discoveries via the agent surface and subscribes to the workspace
lifecycle topic. Its perspective (env vars, curl flows, failure
mitigations) is in
[flyradar/docs/integration-with-flycanon.md](../../flyradar/docs/integration-with-flycanon.md).
The platform narrative ("what these two services are, why they share
a vocabulary") is in
[firefly-intelligence-system.md](../../docs/firefly-intelligence-system.md).

---

## What flycanon offers

flycanon is the **long-lived operational knowledge boundary** of
Firefly OperationOS. Consumers depend on it for:

| Capability | Surface |
|------------|---------|
| Long-lived knowledge repository (Sources -> Candidates -> KnowledgeItems -> Versions) | `/api/v1/sources` + `/api/v1/candidates/*` + `/api/v1/knowledge/*` |
| Hybrid retrieval (BM25 + dense vectors + RRF fusion, optional rerank + query expansion) | `POST /api/v1/search`, `POST /api/v1/agent/search` |
| Grounded RAG answers with citations (and a token-streamed variant) | `POST /api/v1/query`, `POST /api/v1/query/stream`, `POST /api/v1/agent/query`, `POST /api/v1/agent/query/stream` |
| Workspace lifecycle events | EDA topic `canon.workspaces.v1` |
| Append-only audit + cost trails | `GET /api/v1/audit`, `GET /api/v1/billing/events` |
| Universal ingestion (any file format -- PDF text + scanned, Office, archives, images, transcripts, ...) | `POST /api/v1/sources`, `POST /api/v1/agent/sources` |

Full REST surface in [api-reference.md](api-reference.md); wire
payloads in [payload-reference.md](payload-reference.md).

flycanon stops at the data plane -- UIs, workflow surfaces, and SOTA
fetchers are downstream consumers; they subscribe to the
`flycanon.knowledge` / `flycanon.ingest` / `flycanon.audit` topics
and call the REST API.

---

## Agent token surface

Consumers authenticate via long-lived bearer tokens minted by an
operator from flycanon's user-tier
`POST /api/v1/agent-tokens` route. The token model is described in
[api-reference.md § Token management](api-reference.md#token-management);
the DTOs in
[payload-reference.md § Agent tokens](payload-reference.md#agent-tokens).
This section is the producer-side view -- what to ask consumers to
declare, when to grant which scope, how the rotation contract
works.

### Scopes consumers should ask for

Pick the **narrowest** set that covers what the consumer actually
calls. Wildcard (`*`) tokens are a debugging convenience -- not a
production posture.

| Scope | Endpoints | Grant to |
|-------|-----------|----------|
| `agent.sources:ingest` | `POST /api/v1/agent/sources` | Discovery pipelines pushing artefacts (flyradar, batch importers, ...). |
| `agent.sources:read` | `GET /api/v1/agent/sources/{id}` | Operators / consoles that verify intake. |
| `agent.query:run` | `POST /api/v1/agent/query`, `POST /api/v1/agent/query/stream`, `POST /api/v1/agent/search` | Headless RAG callers, copilot services, scheduled report runners. |
| `agent.knowledge:read` | `GET /api/v1/agent/knowledge/{id}`, `.../provenance` | Citation-rendering UIs, downstream provenance tools. |
| `agent.candidates:propose` | `POST /api/v1/agent/candidates:propose` | Consolidation runners that turn sources into candidates. |
| `*` | every scope above | **Wildcard. Mint defaults to this when the request omits scopes -- explicit lists are recommended in production.** |

The verifier matches on per-route scope strings; cross-scope calls
return `403 agent_scope_denied`.

### Workspace allowlist semantics

`workspace_allowlist` on the mint request:

- **`null`** -- the token can be used in any workspace under the
  tenant. Useful for tenant-wide operators (CI runners, ingest
  bots) that may visit many workspaces.
- **`["ws-prod"]`** -- the token can only be used when the request's
  `X-Workspace-Id` equals `"ws-prod"`. Any other workspace returns
  `403 agent_workspace_not_in_allowlist`. **Recommended for
  long-lived production tokens** -- it scopes the blast radius of a
  leak.

Two patterns are common:

1. **One token per workspace.** Mint a separate token per workspace
   the consumer touches. Easy to revoke a single workspace's access.
2. **One token across a fixed set of workspaces.** Mint a token with
   `workspace_allowlist: ["ws-prod", "ws-staging"]`. Easier to
   manage; rotation affects every workspace at once.

### Expiry recommendations

| Use case | Recommended `expires_at` |
|----------|--------------------------|
| Production service-to-service token | 90 days from mint |
| Short-lived CI runner | 24 hours |
| Operator debug token | 1 hour |
| Long-lived integration with formal rotation | 1 year max |

A `null` expiry is permitted by the schema (no enforcement) but
strongly discouraged outside dev. Without an `expires_at` the
revoke path is the only kill switch.

### Rotation procedure (producer view)

When a consumer rotates its token, the cadence on the flycanon side
is:

1. Consumer's operator mints the new token via
   `POST /api/v1/agent-tokens` against the same tenant.
2. Consumer updates its env / secret store and rolls its replicas
   onto the new secret.
3. Operator revokes the old token via
   `DELETE /api/v1/agent-tokens/{old_id}` -- idempotent (already-
   revoked is a 204 no-op).
4. The old token's `revoked_at` is set; subsequent calls fail with
   `invalid_agent_token`.

`last_used_at` on the listing helps find stale tokens that were
issued but never adopted -- safe to revoke.

---

## Subscribing to `canon.workspaces.v1`

Workspace lifecycle events let consumers maintain a local
read-through cache of `(tenant_id, workspace_id)` metadata without
polling `GET /api/v1/workspaces/{id}` on every request.

### Topology

| Concern | Value |
|---------|-------|
| Topic name | `canon.workspaces.v1` (configurable via `FLYCANON_WORKSPACE_TOPIC`) |
| Default broker | `FLYCANON_EDA_ADAPTER=postgres` (durable outbox + LISTEN/NOTIFY) |
| Production alternatives | `redis`, `kafka` -- consumer and producer must agree |
| Delivery | At-least-once; consumers must be idempotent |
| Ordering | Per-subject (tenant, workspace_id) within a partition; not globally |
| Failure mode | Best-effort publish -- a Postgres write commits before the publisher fires. Publish failures are logged and swallowed; consumers can rebuild from `canon_workspaces` or `canon_audit_events` |

The same pyfly `EventPublisher` bean routes the workspace topic and
the existing `flycanon.audit` / `flycanon.knowledge` /
`flycanon.ingest` topics, so a consumer wiring its first
subscription gets the rest "for free" -- see
[eda-events.md](eda-events.md) for the full catalogue.

### Event shapes

Schemas in [payload-reference.md § Workspace lifecycle events](payload-reference.md#workspace-lifecycle-events).
The three event types share a common base
(`tenant_id`, `workspace_id`, `occurred_at`, `event_type`):

| Event | Triggered by | Payload beyond the base |
|-------|--------------|-------------------------|
| `WorkspaceCreated` | `POST /api/v1/workspaces` | `name`, `scope`, `sme_roster`, `retention_days`, `jurisdiction` |
| `WorkspaceUpdated` | `PATCH /api/v1/workspaces/{id}` | same fields as `WorkspaceCreated` (post-update state -- consumers can replace their cached row wholesale) |
| `WorkspaceDeleted` | `POST /api/v1/workspaces/{id}:close` | none -- `(tenant_id, workspace_id)` is enough to evict the cached row |

flycanon has no hard-delete route; `WorkspaceDeleted` is emitted
when a workspace closes (terminal lifecycle state). The row stays
in `canon_workspaces` with `status=closed` for audit. The event
name matches the canonical lifecycle vocabulary so consumers don't
have to special-case the soft-delete semantics.

### Consumer expectations

- **Idempotent on `(event_type, workspace_id, occurred_at)`.**
  Replays during recovery from a broker outage should be safe.
- **Reconcile via `updated_at` on the row.** Concurrent mutations
  may emit out of order; last-write-wins on the projection (the
  `occurred_at` on the event matches the row's `updated_at`).
- **Bounded staleness during outage.** If the broker is down, the
  consumer keeps serving stale entries until its local TTL
  expires; the lazy fill via `GET /api/v1/workspaces/{id}` catches
  up.

flyradar's implementation
(`WorkspaceEventSubscriber` + `WorkspaceCacheClient`) is the
reference pattern -- see
[flyradar/docs/integration-with-flycanon.md § Workspace cache flow](../../flyradar/docs/integration-with-flycanon.md#workspace-cache-flow).

---

## Consumer responsibilities

Every consumer touching the agent surface MUST honour the
firefly-wide [wire-contract conventions](../../docs/firefly-intelligence-system.md#wire-contract-paths)
and the additional rules below.

### Headers on every call

| Header | Required | Why |
|--------|----------|-----|
| `X-Tenant-Id` | yes | flycanon scopes every row by `(tenant_id, workspace_id)`; missing -> `400 missing_tenant_context`. |
| `X-Workspace-Id` | yes | Same. The token's `workspace_allowlist` is checked against this. |
| `X-Agent-Token` | yes (agent routes) | The raw secret as returned by mint. Mutually exclusive with `Authorization`. |
| `X-Correlation-Id` | recommended | Auto-generated when absent. Stitches the consumer-side trace to the flycanon-side trace + audit row. |
| `Idempotency-Key` | **mandatory on every agent POST** | Missing -> `400 missing_idempotency_key`. The user-tier POSTs accept it optionally; agent POSTs are stricter so duplicate machine calls never silently create extra sources / candidates / queries. |

### Error envelope

Every non-2xx is an RFC 7807 problem document. Branch on the
`code` field (snake_case, stable), not the `title` (human-readable,
translation-friendly). Full catalogue in
[api-reference.md § Error responses](api-reference.md#error-responses)
and [api-reference.md § Agent-tier error codes](api-reference.md#agent-tier-error-codes).

The agent-tier failure modes a consumer **must** recognise:

| Code | Status | Consumer action |
|------|--------|-----------------|
| `missing_agent_token` | 401 | Wire the header; the consumer is misconfigured. |
| `invalid_agent_token` | 403 | Rotate or remint; the token was revoked or never existed. |
| `agent_token_expired` | 403 | Rotate per the procedure above. |
| `agent_workspace_not_in_allowlist` | 403 | Verify the consumer's request workspace is in the token's allowlist; remint with a wider allowlist if needed. |
| `agent_scope_denied` | 403 | The token lacks the per-route scope. Remint with the missing scope. |
| `missing_idempotency_key` | 400 | Send the header on every agent POST. |

### Retry policy

flycanon does not opinion on the consumer's retry policy, but the
recommended posture is:

- **Retry 5xx + network errors** with exponential backoff. Reuse the
  same `Idempotency-Key` so flycanon dedups the eventual successful
  write against any partial server-side state.
- **Do NOT retry 4xx.** The consumer is wrong; retrying with the
  same body returns the same 4xx. Fix the request before the next
  attempt.
- **Bound the total retry window** to something shorter than the
  consumer's caller-facing timeout. flycanon's slowest synchronous
  path (`POST /api/v1/agent/sources` with a large PDF) lands in
  ~5-15 seconds; intermediate retries beyond that don't help.

### Audit + observability

Every consumer mutation lands on `canon_audit_events`. The `actor`
column is populated from the agent token (`agent:<prefix>`), so
audit queries can attribute writes back to the minted token.
Consumers should:

- Set `X-Correlation-Id` on outgoing requests so their own
  observability stack and flycanon's audit log share an identifier.
- Treat `agent:<prefix>` as the public identifier of the consumer.
  The prefix is non-secret and safe to log.

---

## Rate limiting

The `rate_limit_rpm` field on the mint request lives on
[`AgentTokenMintRequest`](payload-reference.md#agenttokenmintrequest)
and is enforced by `AgentTokenService.verify` via a per-token sliding
60s counter. A token over its budget returns
`429 rate_limit_exceeded`; `rate_limit_rpm` of `null` or `<= 0`
skips the check.

Consumer guidance:

- Implement exponential backoff on 429 -- the same retry path that
  handles 5xx covers 429 cleanly.
- For multi-replica deployments, the default in-process limiter
  counts per-replica (N replicas effectively multiply the budget
  by N). Opt into the Redis adapter via
  `FLYCANON_RATE_LIMIT_BACKEND=redis` (or `auto` + `FLYCANON_REDIS_URL`)
  to get a single counter shared across replicas, or deploy a
  gateway-level rate limiter in front of the cluster.

---

## Wire-contract stability

flycanon's wire contract is gated by tooling, not convention:

- **OpenAPI snapshot gate.** `flycanon/openapi.json` is committed to
  the repo and verified in CI on every PR. Any drift in path,
  parameter, request body, or response shape fails the build. The
  source of truth is the running service's `/openapi.json`.
- **CalVer release cadence.** Versions follow `YY.MM.PP`. Patch
  releases (`YY.MM.x`) are drop-in -- no schema, env-var, or SDK
  breakage. Monthly releases (`YY.MM.0`) may add optional env vars
  or new endpoints, never break existing ones. Major bumps (year)
  carry an explicit breaking-change note in the
  [CHANGELOG](../CHANGELOG.md).
- **Transition windows.** When a wire-contract change is necessary,
  the old surface stays operational for at least one minor release
  cycle, marked deprecated in the OpenAPI spec, before removal.
- **No service-specific synonyms.** Field names follow the
  [shared vocabulary](../../docs/firefly-intelligence-system.md#shared-vocabulary-post-unification)
  (`tenant_id`, `workspace_id`, `correlation_id`, `actor`,
  `agent_token`); no `engagement_id` aliases, no
  service-specific shorthands.

The
[CHANGELOG.md](../CHANGELOG.md)
is the authoritative per-commit history; consumer engineers should
read it before bumping their pinned flycanon version.

---

## Cross-references

- [firefly-intelligence-system.md](../../docs/firefly-intelligence-system.md) -- platform narrative + shared vocabulary.
- [api-reference.md](api-reference.md) -- the full REST surface.
- [payload-reference.md](payload-reference.md) -- every DTO field-by-field.
- [eda-events.md](eda-events.md) -- the four EDA topics flycanon publishes.
- [architecture.md](architecture.md) -- data model, RLS, workspace events.
- [deployment.md](deployment.md) -- RLS role provisioning, broker configuration, embedding providers.
- [flyradar/docs/integration-with-flycanon.md](../../flyradar/docs/integration-with-flycanon.md) -- the consumer-side view from flyradar.
