# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SDK-local Pydantic models that mirror the service's public DTOs.

We re-declare the models here (instead of importing them from the
service package) so the SDK can be installed in any consumer
codebase without the framework dependency tree.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------
# 26.6 unification constants
# ----------------------------------------------------------------------

#: Topic name for workspace lifecycle events. Mirrors
#: :attr:`flycanon.config.Settings.workspace_topic`. Consumers
#: subscribing to ``WorkspaceCreated`` / ``WorkspaceUpdated`` /
#: ``WorkspaceDeleted`` (see :mod:`flycanon.interfaces.dtos.workspace_event`)
#: bind to this topic.
CANON_WORKSPACES_TOPIC: str = "canon.workspaces.v1"


class ProblemDetails(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str | None = None
    extensions: dict[str, Any] | None = None


class SourceMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    title: str | None = None
    author: str | None = None
    domain: str | None = None
    jurisdiction: str | None = None
    language: str | None = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class SubmitSourceJsonPayload(BaseModel):
    kind: str = "unknown"
    uri: str | None = None
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    content_base64: str | None = None
    filename: str | None = None
    content_type: str | None = None


class SourceRecord(BaseModel):
    id: str
    kind: str
    status: str
    filename: str | None = None
    uri: str | None = None
    content_sha256: str
    content_bytes: int
    n_chunks: int = 0
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    ingested_at: datetime | None = None
    updated_at: datetime


class SourcesPage(BaseModel):
    items: list[SourceRecord]
    total: int
    offset: int
    limit: int


class Citation(BaseModel):
    source_id: str
    chunk_id: str | None = None
    quote: str | None = None
    relevance: float | None = None
    page: int | None = None


class KnowledgeItem(BaseModel):
    id: str
    status: str
    current_version: int
    title: str
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None = None
    summary: str | None = None


class KnowledgeVersion(BaseModel):
    knowledge_item_id: str
    version: int
    status: str
    title: str
    summary: str | None = None
    body: str
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    supersedes_version: int | None = None
    superseded_by_version: int | None = None
    created_by: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeItemsPage(BaseModel):
    items: list[KnowledgeItem]
    total: int
    offset: int
    limit: int


class Provenance(BaseModel):
    knowledge_item_id: str
    version: int
    citations: list[Citation]
    sources: list[dict[str, Any]] = Field(default_factory=list)
    history: list[KnowledgeVersion] = Field(default_factory=list)


class CreateKnowledgeRequest(BaseModel):
    title: str
    body: str
    summary: str | None = None
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    publish: bool = True
    actor: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    summary: str | None = None
    domain: str | None = None
    jurisdiction: str | None = None
    tags: list[str] | None = None
    citations: list[Citation] | None = None
    publish: bool = True
    actor: str | None = None
    metadata: dict[str, Any] | None = None


class SupersedeKnowledgeRequest(BaseModel):
    superseded_by_item_id: str
    reason: str | None = None
    actor: str | None = None


class RetireKnowledgeRequest(BaseModel):
    reason: str
    actor: str | None = None


class CandidateRecord(BaseModel):
    id: str
    status: str
    source_id: str
    title: str
    body: str
    summary: str | None = None
    domain: str
    jurisdiction: str = "GLOBAL"
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    score: float | None = None
    rationale: str | None = None
    materialised_knowledge_item_id: str | None = None
    materialised_version: int | None = None
    actor: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidatesPage(BaseModel):
    items: list[CandidateRecord]
    total: int
    offset: int
    limit: int


class ProposeCandidateRequest(BaseModel):
    source_id: str
    domain: str | None = None
    jurisdiction: str | None = None
    max_chunks: int = 40
    instructions: str | None = None
    actor: str | None = None


class AcceptCandidateRequest(BaseModel):
    target_item_id: str | None = None
    publish: bool = True
    actor: str | None = None
    note: str | None = None


class RejectCandidateRequest(BaseModel):
    reason: str
    actor: str | None = None


class TaxonomyNode(BaseModel):
    id: str
    parent_id: str | None
    slug: str
    label: str
    domain: str
    description: str | None = None
    depth: int
    created_at: datetime
    updated_at: datetime


class TaxonomyTree(BaseModel):
    nodes: list[TaxonomyNode]


class CreateTaxonomyNodeRequest(BaseModel):
    parent_id: str | None = None
    slug: str
    label: str
    domain: str
    description: str | None = None


class Hit(BaseModel):
    """Single retrieval hit.

    Returned by ``/api/v1/search`` as the page-level list and by
    ``/api/v1/query`` as the citation list. The structured source-side
    fields (``source_filename`` / ``source_title`` / ``source_kind`` /
    ``source_uri`` / ``section_path`` / ``page``) are hydrated by
    flycanon so the SDK consumer can render citation labels without a
    second ``GET /api/v1/sources/{id}`` round-trip.
    """

    chunk_id: str
    source_id: str
    source_filename: str | None = None
    source_title: str | None = None
    source_kind: str | None = None
    source_uri: str | None = None
    section_path: str | None = None
    page: int | None = None
    knowledge_item_id: str | None = None
    knowledge_version: int | None = None
    content: str
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    per_query_k: int | None = None
    source_ids: list[str] | None = None
    knowledge_item_ids: list[str] | None = None
    domains: list[str] | None = None
    jurisdictions: list[str] | None = None
    tags: list[str] | None = None
    statuses: list[str] | None = None


class SearchResponse(BaseModel):
    hits: list[Hit]
    elapsed_ms: int


class AnswerRequest(BaseModel):
    question: str
    top_k: int = 8
    instructions: str | None = None
    model: str | None = None
    source_ids: list[str] | None = None
    knowledge_item_ids: list[str] | None = None
    domains: list[str] | None = None
    jurisdictions: list[str] | None = None
    tags: list[str] | None = None
    statuses: list[str] | None = None


class AnswerResponse(BaseModel):
    answer: str
    citations: list[Hit]
    model: str
    elapsed_ms: int
    no_answer: bool = False


class AuditEvent(BaseModel):
    id: str
    occurred_at: datetime
    event_type: str
    actor: str | None = None
    subject_id: str
    subject_kind: str
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    items: list[AuditEvent]
    total: int
    offset: int
    limit: int


class VersionInfo(BaseModel):
    service: str
    version: str
    embedding_model: str
    answer_model: str
    answer_fallback_model: str
    vector_store: str
    eda_adapter: str


# ----------------------------------------------------------------------
# Tier 1 / Tier 2 extensions
# ----------------------------------------------------------------------


class BulkSourceResult(BaseModel):
    """Per-item outcome of a bulk ingest."""

    index: int
    ok: bool
    source_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class BulkSourcesResponse(BaseModel):
    items: list[BulkSourceResult]
    total: int
    succeeded: int
    failed: int


class IngestJob(BaseModel):
    """Header view of an async ingest job."""

    id: str
    status: str
    progress: float = 0.0
    stage: str | None = None
    source_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class IngestJobEvent(BaseModel):
    """One SSE frame, parsed."""

    cursor: int
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class KnowledgeFieldChange(BaseModel):
    field: str
    from_value: Any | None = Field(default=None, alias="from")
    to_value: Any | None = Field(default=None, alias="to")

    model_config = ConfigDict(populate_by_name=True)


class KnowledgeDiff(BaseModel):
    knowledge_item_id: str
    from_version: int
    to_version: int
    unified_diff: str
    field_changes: list[KnowledgeFieldChange] = Field(default_factory=list)
    added_citations: list[str] = Field(default_factory=list)
    removed_citations: list[str] = Field(default_factory=list)


class KnowledgeRelation(BaseModel):
    id: str
    from_item_id: str
    to_item_id: str
    kind: str
    since_version: int | None = None
    note: str | None = None
    actor: str | None = None
    created_at: datetime


class RelationsList(BaseModel):
    outgoing: list[KnowledgeRelation] = Field(default_factory=list)
    incoming: list[KnowledgeRelation] = Field(default_factory=list)


class CreateRelationRequest(BaseModel):
    to_item_id: str
    kind: str
    since_version: int | None = None
    note: str | None = None
    actor: str | None = None


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str
    domain: str | None = None


class GraphEdge(BaseModel):
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    kind: str

    model_config = ConfigDict(populate_by_name=True)


class KnowledgeGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class ConversationHeader(BaseModel):
    id: str
    title: str | None = None
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationTurn(BaseModel):
    id: str
    conversation_id: str
    query: str
    answer: str
    citations: list[Hit] = Field(default_factory=list)
    model: str | None = None
    created_at: datetime


class Conversation(BaseModel):
    id: str
    title: str | None = None
    summary: str | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateConversationRequest(BaseModel):
    title: str | None = None
    actor: str | None = None


class CreateConversationTurnRequest(BaseModel):
    query: str
    max_chunks: int | None = None
    hybrid_mode: str | None = None
    actor: str | None = None


class SuggestionsResponse(BaseModel):
    questions: list[str] = Field(default_factory=list)


class StaleItem(BaseModel):
    knowledge_item_id: str
    title: str
    domain: str
    score: float | None = None
    max_similarity: float | None = None
    sample_size: int = 0
    computed_at: str


class StaleReport(BaseModel):
    items: list[StaleItem] = Field(default_factory=list)
    total: int = 0


class ConflictScanRequest(BaseModel):
    domain: str | None = None
    min_similarity: float = 0.85
    max_items: int = 50
    actor: str | None = None


class ConflictScanResponse(BaseModel):
    pairs_evaluated: int = 0
    conflicts_found: int = 0
    candidate_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)


class BillingRow(BaseModel):
    group: dict[str, Any] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: str = "0"
    calls: int = 0


class BillingReport(BaseModel):
    rows: list[BillingRow] = Field(default_factory=list)
    total_cost_usd: str = "0"
    total_calls: int = 0


class CostEvent(BaseModel):
    """Single ``canon_cost_events`` row, surfaced as a DTO."""

    id: int
    agent_name: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: str
    latency_ms: int | None = None
    actor: str | None = None
    correlation_id: str | None = None
    subject_kind: str | None = None
    subject_id: str | None = None
    occurred_at: datetime


class CostEventsPage(BaseModel):
    rows: list[CostEvent] = Field(default_factory=list)
    limit: int
    offset: int


class BillingWindow(BaseModel):
    since: datetime
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: str = "0"
    top_model: str | None = None
    top_model_cost_usd: str = "0"
    top_actor: str | None = None
    top_actor_cost_usd: str = "0"


class BillingSummary(BaseModel):
    generated_at: datetime
    last_24h: BillingWindow
    last_7d: BillingWindow
    last_30d: BillingWindow


class TopConsumerRow(BaseModel):
    dimension: str
    value: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: str = "0"
    calls: int = 0


class TopConsumersReport(BaseModel):
    dimension: str
    rows: list[TopConsumerRow] = Field(default_factory=list)


class SubjectCostRow(BaseModel):
    subject_kind: str | None = None
    subject_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: str = "0"
    calls: int = 0


class SubjectCostReport(BaseModel):
    rows: list[SubjectCostRow] = Field(default_factory=list)


class LatencyRow(BaseModel):
    group: dict[str, str] = Field(default_factory=dict)
    count: int = 0
    avg_ms: int = 0
    p50_ms: int = 0
    p95_ms: int = 0
    p99_ms: int = 0
    max_ms: int = 0


class LatencyReport(BaseModel):
    rows: list[LatencyRow] = Field(default_factory=list)


class SourceStats(BaseModel):
    total: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    total_bytes: int = 0


class KnowledgeItemStats(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_domain: dict[str, int] = Field(default_factory=dict)


class CandidateStats(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)


class ChunkStats(BaseModel):
    total: int = 0
    embedded: int = 0
    embedded_pct: float = 0.0


class JobStats(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    avg_attempts: float = 0.0


class CostStreamStats(BaseModel):
    total_events: int = 0
    cost_usd_24h: str = "0"
    cost_usd_30d: str = "0"


class CorpusStats(BaseModel):
    generated_at: datetime
    sources: SourceStats
    knowledge_items: KnowledgeItemStats
    knowledge_versions: int = 0
    candidates: CandidateStats
    chunks: ChunkStats
    ingest_jobs: JobStats
    cost: CostStreamStats


# ----------------------------------------------------------------------
# 26.6 unification -- Workspace CRUD DTOs
# ----------------------------------------------------------------------


class WorkspaceCreate(BaseModel):
    """Request body for ``POST /api/v1/workspaces``.

    ``id`` is caller-chosen (slug, ``<= 64`` chars); the tenant scope
    comes from the ``X-Tenant-Id`` header so a caller cannot create a
    workspace under another tenant.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    status: str = "active"
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = Field(default=None, ge=1)
    jurisdiction: str | None = Field(default=None, max_length=64)


class WorkspaceUpdate(BaseModel):
    """Request body for ``PATCH /api/v1/workspaces/{id}``.

    Every field is optional; only fields present in the payload are
    applied server-side.
    """

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = None
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = Field(default=None, ge=1)
    jurisdiction: str | None = Field(default=None, max_length=64)


class WorkspaceSpec(BaseModel):
    """Canonical workspace shape returned by ``GET /api/v1/workspaces/{id}``.

    Mirrors :class:`flycanon.interfaces.dtos.workspace.WorkspaceSpec` --
    the ``scope`` / ``sme_roster`` fields are the JSON shapes as
    stored (the row columns are suffixed ``_json``, but the wire
    DTO uses the plain names).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    name: str
    status: str
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = None
    jurisdiction: str | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class WorkspaceSummary(BaseModel):
    """Compact row shape from ``GET /api/v1/workspaces`` -- omits
    ``scope`` + ``sme_roster`` details."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    name: str
    status: str
    retention_days: int | None = None
    jurisdiction: str | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


# ----------------------------------------------------------------------
# 26.6 unification -- Agent token CRUD DTOs
# ----------------------------------------------------------------------


class AgentTokenMintRequest(BaseModel):
    """Request body for ``POST /api/v1/agent-tokens``.

    ``workspace_allowlist`` is optional -- ``None`` means the token
    can be used in any workspace under the tenant. ``scopes``
    defaults to ``["*"]`` (all scopes); the verify path treats
    ``"*"`` as a wildcard.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=128)
    workspace_allowlist: list[str] | None = None
    scopes: list[str] = Field(default_factory=lambda: ["*"])
    rate_limit_rpm: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: datetime | None = None


class AgentTokenSummary(BaseModel):
    """Token surface as listed back to user-tier callers -- secret omitted.

    Mirrors :class:`flycanon.interfaces.dtos.agent_token.AgentTokenSummaryDto`.
    Every read path through ``GET /api/v1/agent-tokens`` returns
    this shape; the raw token is only available on
    :class:`AgentTokenCreated` (mint response).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    prefix: str
    workspace_allowlist: list[str] | None
    scopes: list[str]
    rate_limit_rpm: int | None
    expires_at: datetime | None
    created_at: datetime
    created_by: str
    revoked_at: datetime | None
    last_used_at: datetime | None


class AgentTokenCreated(AgentTokenSummary):
    """Mint response: includes the full ``token`` ONCE.

    Subsequent reads only ever expose ``prefix`` -- the secret hash
    is stored server-side and never returned. Callers MUST capture
    ``token`` at mint time; there is no recovery path.
    """

    model_config = ConfigDict(frozen=True)

    token: str


# ----------------------------------------------------------------------
# 26.6 unification -- Workspace lifecycle event DTOs
# ----------------------------------------------------------------------


class _WorkspaceEventBase(BaseModel):
    """Shared fields for every workspace lifecycle event."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    occurred_at: datetime


class WorkspaceCreated(_WorkspaceEventBase):
    """A new workspace row landed in ``canon_workspaces``.

    Emitted from ``POST /api/v1/workspaces``. Payload mirrors the
    wire :class:`WorkspaceSpec` so consumers can rebuild a cache
    row directly from the event body.
    """

    event_type: Literal["workspace.created"] = "workspace.created"
    name: str = Field(min_length=1, max_length=255)
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = None
    jurisdiction: str | None = None


class WorkspaceUpdated(_WorkspaceEventBase):
    """An existing workspace row was sparsely patched.

    Emitted from ``PATCH /api/v1/workspaces/{id}``. The event
    carries the **post-update** row state (not just the patched
    fields) so consumers can replace cached rows wholesale.
    """

    event_type: Literal["workspace.updated"] = "workspace.updated"
    name: str = Field(min_length=1, max_length=255)
    scope: list[Any] | None = None
    sme_roster: list[Any] | None = None
    retention_days: int | None = None
    jurisdiction: str | None = None


class WorkspaceDeleted(_WorkspaceEventBase):
    """A workspace was closed (flycanon's terminal lifecycle state).

    Emitted from ``POST /api/v1/workspaces/{id}:close``. flycanon
    has no hard-delete route -- closing is the terminal transition;
    the row is preserved for audit. The event name ``workspace.deleted``
    matches the canonical lifecycle vocabulary so downstream
    consumers don't have to special-case flycanon's soft-delete
    semantics.
    """

    event_type: Literal["workspace.deleted"] = "workspace.deleted"
