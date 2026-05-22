# Copyright 2026 Firefly Software Solutions Inc
"""``CanonClient`` -- async client for every flycanon endpoint.

Pooled httpx.AsyncClient under the hood. Use as an async context
manager to get clean connection lifecycle:

    async with CanonClient(base_url="http://localhost:8500") as client:
        ...

The 26.6.0 unification adds four firefly wire-contract headers
(``X-Tenant-Id``, ``X-Workspace-Id``, ``X-Correlation-Id``,
``X-Agent-Token``) on the constructor; every outbound request
sends whichever of those four are configured. All four are
optional on the SDK side -- the service rejects missing
tenant/workspace headers at the boundary; the SDK just forwards
what it has.

The agent-tier surface is exposed via :attr:`CanonClient.agent`:

    client = CanonClient(base_url="...", agent_token="canon_...")
    resp = await client.agent.query(answer_request, idempotency_key="...")

Agent POSTs MUST carry ``Idempotency-Key`` (the service enforces
``400 missing_idempotency_key`` on missing key) -- the SDK
validates the argument is non-empty before dispatching the
request to give the caller a fast local error.
"""

from __future__ import annotations

import json as _jsonlib
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from types import TracebackType
from typing import Any

import httpx

from flycanon_sdk._errors import (
    CanonAPIError,
    CanonConnectionError,
    _parse_field_errors,
    _resolve_api_error_class,
)
from flycanon_sdk._models import (
    AcceptCandidateRequest,
    AgentTokenCreated,
    AgentTokenMintRequest,
    AgentTokenSummary,
    AnswerRequest,
    AnswerResponse,
    AuditPage,
    BillingReport,
    BillingSummary,
    BulkSourcesResponse,
    CandidateRecord,
    CandidatesPage,
    ConflictScanRequest,
    ConflictScanResponse,
    Conversation,
    ConversationTurn,
    CorpusStats,
    CostEventsPage,
    CreateConversationRequest,
    CreateConversationTurnRequest,
    CreateKnowledgeRequest,
    CreateRelationRequest,
    CreateTaxonomyNodeRequest,
    IngestJob,
    IngestJobEvent,
    KnowledgeDiff,
    KnowledgeGraph,
    KnowledgeItem,
    KnowledgeItemsPage,
    KnowledgeRelation,
    KnowledgeVersion,
    LatencyReport,
    ProposeCandidateRequest,
    Provenance,
    RejectCandidateRequest,
    RelationsList,
    RetireKnowledgeRequest,
    SearchRequest,
    SearchResponse,
    SourceRecord,
    SourcesPage,
    StaleReport,
    SubjectCostReport,
    SubmitSourceJsonPayload,
    SuggestionsResponse,
    SupersedeKnowledgeRequest,
    TaxonomyNode,
    TaxonomyTree,
    TopConsumersReport,
    UpdateKnowledgeRequest,
    VersionInfo,
    WorkspaceCreate,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
)

# ----------------------------------------------------------------------
# Canonical firefly wire-contract header names
# ----------------------------------------------------------------------

_HEADER_TENANT_ID = "X-Tenant-Id"
_HEADER_WORKSPACE_ID = "X-Workspace-Id"
_HEADER_CORRELATION_ID = "X-Correlation-Id"
_HEADER_AGENT_TOKEN = "X-Agent-Token"
_HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"


class CanonClient:
    """Async HTTP client for the flycanon service."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
        agent_token: str | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Construct a client.

        :param base_url: Service base URL (``http://localhost:8500``).
        :param api_key: Optional bearer token sent as ``Authorization``.
        :param tenant_id: Optional ``X-Tenant-Id`` value sent on every
            request.
        :param workspace_id: Optional ``X-Workspace-Id`` value sent on
            every request.
        :param correlation_id: Optional ``X-Correlation-Id`` value sent
            on every request. Usually rotated per call by the caller.
        :param agent_token: Optional ``X-Agent-Token`` value sent on
            every request -- only meaningful for ``/api/v1/agent/*``
            routes.
        :param timeout: httpx client timeout (seconds).
        :param client: Caller-supplied ``httpx.AsyncClient`` to reuse.
            When provided the SDK does not close it on ``aclose()``.
        :param headers: Extra headers merged into every request.
        """
        merged_headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "flycanon-sdk-python/26.6.0",
        }
        if api_key:
            merged_headers["Authorization"] = f"Bearer {api_key}"
        if tenant_id:
            merged_headers[_HEADER_TENANT_ID] = tenant_id
        if workspace_id:
            merged_headers[_HEADER_WORKSPACE_ID] = workspace_id
        if correlation_id:
            merged_headers[_HEADER_CORRELATION_ID] = correlation_id
        if agent_token:
            merged_headers[_HEADER_AGENT_TOKEN] = agent_token
        if headers:
            merged_headers.update(headers)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=merged_headers,
        )
        # Stash for downstream sub-clients (agent surface).
        self._tenant_id = tenant_id
        self._workspace_id = workspace_id
        self._correlation_id = correlation_id
        self._agent_token = agent_token
        # Agent-tier sub-client. Lazy attribute -- created at construct
        # time so ``client.agent`` is always available without an extra
        # method call.
        self.agent: AgentSurface = AgentSurface(self)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> CanonClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------

    async def version(self) -> VersionInfo:
        return VersionInfo.model_validate(await self._request("GET", "/api/v1/version"))

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    async def submit_source(self, payload: SubmitSourceJsonPayload) -> SourceRecord:
        body = await self._request(
            "POST",
            "/api/v1/sources",
            json=payload.model_dump(exclude_none=True),
        )
        return SourceRecord.model_validate(body)

    async def get_source(self, source_id: str) -> SourceRecord:
        body = await self._request("GET", f"/api/v1/sources/{source_id}")
        return SourceRecord.model_validate(body)

    async def list_sources(
        self,
        *,
        status: Iterable[str] | None = None,
        kind: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SourcesPage:
        body = await self._request(
            "GET",
            "/api/v1/sources",
            params={
                "status": _csv(status),
                "kind": _csv(kind),
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return SourcesPage.model_validate(body)

    async def submit_sources_bulk(
        self,
        payloads: list[SubmitSourceJsonPayload],
    ) -> BulkSourcesResponse:
        """Submit multiple sources in one request -- returns per-item results."""
        body = await self._request(
            "POST",
            "/api/v1/sources:bulk",
            json={"items": [p.model_dump(exclude_none=True) for p in payloads]},
        )
        return BulkSourcesResponse.model_validate(body)

    async def submit_source_async(
        self,
        payload: SubmitSourceJsonPayload,
    ) -> IngestJob:
        """Enqueue an async ingest job. Stream progress on ``stream_job``."""
        body = await self._request(
            "POST",
            "/api/v1/sources:async",
            json=payload.model_dump(exclude_none=True),
        )
        return IngestJob.model_validate(body)

    async def replace_source(
        self,
        source_id: str,
        payload: SubmitSourceJsonPayload,
    ) -> SourceRecord:
        """Re-ingest an existing source in place, preserving the row id."""
        body = await self._request(
            "PUT",
            f"/api/v1/sources/{source_id}",
            json=payload.model_dump(exclude_none=True),
        )
        return SourceRecord.model_validate(body)

    # ------------------------------------------------------------------
    # Async ingest jobs (path renamed in 26.6.0: /jobs -> /ingest-jobs)
    # ------------------------------------------------------------------

    async def get_job(self, job_id: str) -> IngestJob:
        body = await self._request("GET", f"/api/v1/ingest-jobs/{job_id}")
        return IngestJob.model_validate(body)

    async def cancel_job(self, job_id: str) -> IngestJob:
        body = await self._request("POST", f"/api/v1/ingest-jobs/{job_id}:cancel")
        return IngestJob.model_validate(body)

    def stream_job(
        self,
        job_id: str,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[IngestJobEvent]:
        """Stream Server-Sent Events for a job. Reconnect with ``cursor``.

        Returns an async iterator directly -- use as
        ``async for ev in client.stream_job(job_id): ...``.
        """
        return self._sse(f"/api/v1/ingest-jobs/{job_id}/stream", params={"cursor": cursor})

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    async def create_knowledge(self, request: CreateKnowledgeRequest) -> KnowledgeVersion:
        body = await self._request("POST", "/api/v1/knowledge", json=request.model_dump(exclude_none=True))
        return KnowledgeVersion.model_validate(body)

    async def update_knowledge(self, item_id: str, request: UpdateKnowledgeRequest) -> KnowledgeVersion:
        body = await self._request(
            "PUT",
            f"/api/v1/knowledge/{item_id}",
            json=request.model_dump(exclude_none=True),
        )
        return KnowledgeVersion.model_validate(body)

    async def supersede_knowledge(self, item_id: str, request: SupersedeKnowledgeRequest) -> KnowledgeItem:
        body = await self._request(
            "POST",
            f"/api/v1/knowledge/{item_id}:supersede",
            json=request.model_dump(exclude_none=True),
        )
        return KnowledgeItem.model_validate(body)

    async def retire_knowledge(self, item_id: str, request: RetireKnowledgeRequest) -> KnowledgeItem:
        body = await self._request(
            "POST",
            f"/api/v1/knowledge/{item_id}:retire",
            json=request.model_dump(exclude_none=True),
        )
        return KnowledgeItem.model_validate(body)

    async def get_knowledge(self, item_id: str) -> KnowledgeItem:
        body = await self._request("GET", f"/api/v1/knowledge/{item_id}")
        return KnowledgeItem.model_validate(body)

    async def list_knowledge_items(
        self,
        *,
        status: Iterable[str] | None = None,
        domain: Iterable[str] | None = None,
        jurisdiction: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> KnowledgeItemsPage:
        body = await self._request(
            "GET",
            "/api/v1/knowledge",
            params={
                "status": _csv(status),
                "domain": _csv(domain),
                "jurisdiction": _csv(jurisdiction),
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return KnowledgeItemsPage.model_validate(body)

    async def get_history(self, item_id: str) -> list[KnowledgeVersion]:
        body = await self._request("GET", f"/api/v1/knowledge/{item_id}/history")
        return [KnowledgeVersion.model_validate(row) for row in body]

    async def get_provenance(
        self,
        item_id: str,
        version: int | None = None,
    ) -> Provenance:
        body = await self._request(
            "GET",
            f"/api/v1/knowledge/{item_id}/provenance",
            params={"version": str(version)} if version else None,
        )
        return Provenance.model_validate(body)

    async def get_diff(
        self,
        item_id: str,
        *,
        from_version: int,
        to_version: int,
    ) -> KnowledgeDiff:
        body = await self._request(
            "GET",
            f"/api/v1/knowledge/{item_id}/diff",
            params={
                "from_version": str(from_version),
                "to_version": str(to_version),
            },
        )
        return KnowledgeDiff.model_validate(body)

    # ------------------------------------------------------------------
    # Knowledge graph (relations + graph view)
    # ------------------------------------------------------------------

    async def list_relations(self, item_id: str) -> RelationsList:
        body = await self._request("GET", f"/api/v1/knowledge/{item_id}/relations")
        return RelationsList.model_validate(body)

    async def add_relation(
        self,
        item_id: str,
        request: CreateRelationRequest,
    ) -> KnowledgeRelation:
        body = await self._request(
            "POST",
            f"/api/v1/knowledge/{item_id}/relations",
            json=request.model_dump(exclude_none=True),
        )
        return KnowledgeRelation.model_validate(body)

    async def remove_relation(self, relation_id: str) -> None:
        await self._request("DELETE", f"/api/v1/knowledge/relations/{relation_id}")

    async def get_graph(
        self,
        *,
        domain: str | None = None,
        kind: str | None = None,
        include_sources: bool = False,
    ) -> KnowledgeGraph:
        body = await self._request(
            "GET",
            "/api/v1/knowledge:graph",
            params={
                "domain": domain,
                "kind": kind,
                "include_sources": "true" if include_sources else "false",
            },
        )
        return KnowledgeGraph.model_validate(body)

    async def get_graph_mermaid(
        self,
        *,
        domain: str | None = None,
        kind: str | None = None,
        include_sources: bool = False,
    ) -> str:
        """Return the graph as a Mermaid string (``graph LR ...``)."""
        cleaned = {
            k: v
            for k, v in {
                "domain": domain,
                "kind": kind,
                "include_sources": "true" if include_sources else "false",
            }.items()
            if v not in (None, "")
        }
        try:
            response = await self._client.request(
                "GET",
                "/api/v1/knowledge:graph",
                params=cleaned,
                headers={"Accept": "text/vnd.mermaid"},
            )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise CanonConnectionError(str(exc)) from exc
        if 200 <= response.status_code < 300:
            return response.text
        await _raise_for_problem(response)
        return ""  # unreachable

    # ------------------------------------------------------------------
    # Knowledge quality scans
    # ------------------------------------------------------------------

    async def scan_stale(self) -> StaleReport:
        body = await self._request("GET", "/api/v1/knowledge:stale")
        return StaleReport.model_validate(body)

    async def detect_conflicts(
        self,
        request: ConflictScanRequest | None = None,
    ) -> ConflictScanResponse:
        payload = (request or ConflictScanRequest()).model_dump(exclude_none=True)
        body = await self._request("POST", "/api/v1/knowledge:detect-conflicts", json=payload)
        return ConflictScanResponse.model_validate(body)

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    async def propose_candidates(self, request: ProposeCandidateRequest) -> list[CandidateRecord]:
        body = await self._request(
            "POST",
            "/api/v1/candidates:propose",
            json=request.model_dump(exclude_none=True),
        )
        return [CandidateRecord.model_validate(row) for row in body]

    async def list_candidates(
        self,
        *,
        status: Iterable[str] | None = None,
        source_id: str | None = None,
        domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CandidatesPage:
        body = await self._request(
            "GET",
            "/api/v1/candidates",
            params={
                "status": _csv(status),
                "source_id": source_id,
                "domain": domain,
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return CandidatesPage.model_validate(body)

    async def get_candidate(self, candidate_id: str) -> CandidateRecord:
        body = await self._request("GET", f"/api/v1/candidates/{candidate_id}")
        return CandidateRecord.model_validate(body)

    async def accept_candidate(self, candidate_id: str, request: AcceptCandidateRequest) -> CandidateRecord:
        body = await self._request(
            "POST",
            f"/api/v1/candidates/{candidate_id}:accept",
            json=request.model_dump(exclude_none=True),
        )
        return CandidateRecord.model_validate(body)

    async def reject_candidate(self, candidate_id: str, request: RejectCandidateRequest) -> CandidateRecord:
        body = await self._request(
            "POST",
            f"/api/v1/candidates/{candidate_id}:reject",
            json=request.model_dump(exclude_none=True),
        )
        return CandidateRecord.model_validate(body)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        per_query_k: int | None = None,
        source_ids: list[str] | None = None,
    ) -> SearchResponse:
        request = SearchRequest(
            query=query,
            top_k=top_k,
            per_query_k=per_query_k,
            source_ids=source_ids,
        )
        body = await self._request("POST", "/api/v1/search", json=request.model_dump(exclude_none=True))
        return SearchResponse.model_validate(body)

    async def answer(
        self,
        question: str,
        *,
        top_k: int = 8,
        instructions: str | None = None,
        model: str | None = None,
    ) -> AnswerResponse:
        request = AnswerRequest(
            question=question,
            top_k=top_k,
            instructions=instructions,
            model=model,
        )
        body = await self._request("POST", "/api/v1/query", json=request.model_dump(exclude_none=True))
        return AnswerResponse.model_validate(body)

    def stream_answer(
        self,
        question: str,
        *,
        top_k: int = 8,
        instructions: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[IngestJobEvent]:
        """Stream the answer endpoint as Server-Sent Events.

        Each yielded ``IngestJobEvent`` re-uses the generic frame shape
        ``(cursor, event, data)``. The ``token`` events carry
        ``{"text": "..."}``; the final ``complete`` event carries
        ``{"answer": "...", "citations": [...]}``. Returns an async
        iterator directly -- use as
        ``async for frame in client.stream_answer(question): ...``.
        """
        request = AnswerRequest(
            question=question,
            top_k=top_k,
            instructions=instructions,
            model=model,
        )
        return self._sse(
            "/api/v1/query:stream",
            json=request.model_dump(exclude_none=True),
            method="POST",
        )

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        request: CreateConversationRequest | None = None,
    ) -> Conversation:
        payload = (request or CreateConversationRequest()).model_dump(exclude_none=True)
        body = await self._request("POST", "/api/v1/conversations", json=payload)
        return Conversation.model_validate(body)

    async def get_conversation(self, conversation_id: str) -> Conversation:
        body = await self._request("GET", f"/api/v1/conversations/{conversation_id}")
        return Conversation.model_validate(body)

    async def add_turn(
        self,
        conversation_id: str,
        request: CreateConversationTurnRequest,
    ) -> ConversationTurn:
        body = await self._request(
            "POST",
            f"/api/v1/conversations/{conversation_id}/turns",
            json=request.model_dump(exclude_none=True),
        )
        return ConversationTurn.model_validate(body)

    async def list_turns(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationTurn]:
        body = await self._request(
            "GET",
            f"/api/v1/conversations/{conversation_id}/turns",
            params={"limit": str(limit), "offset": str(offset)},
        )
        rows = body.get("items", body) if isinstance(body, dict) else body
        return [ConversationTurn.model_validate(r) for r in rows]

    async def suggest_questions(
        self,
        conversation_id: str,
    ) -> SuggestionsResponse:
        body = await self._request("POST", f"/api/v1/conversations/{conversation_id}/suggest")
        return SuggestionsResponse.model_validate(body)

    # ------------------------------------------------------------------
    # Billing
    # ------------------------------------------------------------------

    async def billing_report(
        self,
        *,
        group_by: Iterable[str] | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> BillingReport:
        body = await self._request(
            "GET",
            "/api/v1/billing",
            params={
                "group_by": _csv(group_by) if group_by else None,
                "actor": actor,
                "since": _iso(since),
                "until": _iso(until),
            },
        )
        return BillingReport.model_validate(body)

    async def list_cost_events(
        self,
        *,
        actor: str | None = None,
        agent_name: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CostEventsPage:
        """Per-call drill-down of the cost stream."""
        body = await self._request(
            "GET",
            "/api/v1/billing/events",
            params={
                "actor": actor,
                "agent_name": agent_name,
                "since": _iso(since),
                "until": _iso(until),
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return CostEventsPage.model_validate(body)

    async def billing_summary(
        self,
        *,
        actor: str | None = None,
    ) -> BillingSummary:
        """Rolling-window cost snapshot (24h / 7d / 30d)."""
        body = await self._request(
            "GET",
            "/api/v1/billing/summary",
            params={"actor": actor},
        )
        return BillingSummary.model_validate(body)

    async def billing_top(
        self,
        *,
        dimension: str = "model",
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 10,
    ) -> TopConsumersReport:
        """Top-N consumers on a single dimension."""
        body = await self._request(
            "GET",
            "/api/v1/billing/top",
            params={
                "dimension": dimension,
                "since": _iso(since),
                "until": _iso(until),
                "limit": str(limit),
            },
        )
        return TopConsumersReport.model_validate(body)

    async def billing_by_subject(
        self,
        *,
        subject_kind: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 20,
    ) -> SubjectCostReport:
        """Cost attribution per ``(subject_kind, subject_id)``."""
        body = await self._request(
            "GET",
            "/api/v1/billing/by-subject",
            params={
                "subject_kind": subject_kind,
                "since": _iso(since),
                "until": _iso(until),
                "limit": str(limit),
            },
        )
        return SubjectCostReport.model_validate(body)

    async def billing_latency(
        self,
        *,
        group_by: Iterable[str] | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> LatencyReport:
        """p50 / p95 / p99 latency per group bucket."""
        body = await self._request(
            "GET",
            "/api/v1/billing/latency",
            params={
                "group_by": _csv(group_by) if group_by else None,
                "since": _iso(since),
                "until": _iso(until),
            },
        )
        return LatencyReport.model_validate(body)

    # ------------------------------------------------------------------
    # Corpus inventory
    # ------------------------------------------------------------------

    async def stats(self) -> CorpusStats:
        """One-shot corpus + queue + cost-stream snapshot."""
        body = await self._request("GET", "/api/v1/stats")
        return CorpusStats.model_validate(body)

    # ------------------------------------------------------------------
    # Taxonomy
    # ------------------------------------------------------------------

    async def get_taxonomy(self) -> TaxonomyTree:
        body = await self._request("GET", "/api/v1/taxonomy")
        return TaxonomyTree.model_validate(body)

    async def create_taxonomy_node(self, request: CreateTaxonomyNodeRequest) -> TaxonomyNode:
        body = await self._request(
            "POST",
            "/api/v1/taxonomy/nodes",
            json=request.model_dump(exclude_none=True),
        )
        return TaxonomyNode.model_validate(body)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def list_audit(
        self,
        *,
        subject_id: str | None = None,
        subject_kind: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditPage:
        body = await self._request(
            "GET",
            "/api/v1/audit",
            params={
                "subject_id": subject_id,
                "subject_kind": subject_kind,
                "event_type": event_type,
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return AuditPage.model_validate(body)

    # ------------------------------------------------------------------
    # Workspaces (26.6 unification -- Plan 4 user-tier CRUD)
    # ------------------------------------------------------------------

    async def create_workspace(self, spec: WorkspaceCreate) -> WorkspaceSpec:
        """Create a workspace under the caller's tenant.

        The id comes from ``spec.id`` (caller-chosen slug); the tenant
        comes from the ``X-Tenant-Id`` header. Returns ``201 Created``
        with the persisted :class:`WorkspaceSpec`.
        """
        body = await self._request(
            "POST",
            "/api/v1/workspaces",
            json=spec.model_dump(exclude_none=True),
        )
        return WorkspaceSpec.model_validate(body)

    async def list_workspaces(self) -> list[WorkspaceSummary]:
        """Return every workspace owned by the caller's tenant.

        Ordered ``created_at DESC`` by the repository contract; the
        most-recently-opened workspaces appear first.
        """
        body = await self._request("GET", "/api/v1/workspaces")
        return [WorkspaceSummary.model_validate(row) for row in body]

    async def get_workspace(self, workspace_id: str) -> WorkspaceSpec:
        """Fetch a single workspace by id within the caller's tenant.

        Raises ``CanonAPIError(code='workspace_not_found')`` when the
        ``(tenant_id, workspace_id)`` pair does not exist.
        """
        body = await self._request("GET", f"/api/v1/workspaces/{workspace_id}")
        return WorkspaceSpec.model_validate(body)

    async def update_workspace(
        self,
        workspace_id: str,
        patch: WorkspaceUpdate,
    ) -> WorkspaceSpec:
        """Apply a sparse patch to a workspace row.

        Only fields present in ``patch`` (``exclude_unset=True``) are
        applied; everything else is preserved server-side.
        """
        body = await self._request(
            "PATCH",
            f"/api/v1/workspaces/{workspace_id}",
            json=patch.model_dump(exclude_unset=True, exclude_none=True),
        )
        return WorkspaceSpec.model_validate(body)

    async def close_workspace(self, workspace_id: str) -> WorkspaceSpec:
        """Close a workspace (status='closed' + closed_at=now()).

        Idempotent at the row level: closing an already-closed
        workspace rewrites the same terminal state with a fresh
        ``closed_at`` and returns the current row. The close call is
        flycanon's terminal lifecycle transition -- downstream event
        consumers see this as the workspace "delete" signal.
        """
        body = await self._request(
            "POST",
            f"/api/v1/workspaces/{workspace_id}:close",
        )
        return WorkspaceSpec.model_validate(body)

    # ------------------------------------------------------------------
    # Agent tokens (26.6 unification -- user-tier CRUD surface)
    # ------------------------------------------------------------------

    async def mint_agent_token(self, request: AgentTokenMintRequest) -> AgentTokenCreated:
        """Mint a new agent token under the caller's tenant.

        The full ``token`` is returned **once** in the response;
        subsequent reads (:meth:`list_agent_tokens`) only expose the
        public ``prefix``. Callers MUST capture the token here --
        there is no recovery path.

        Refuses agent-tier callers (``X-Agent-Token``-authenticated)
        with ``403 agent_cannot_mint``.
        """
        body = await self._request(
            "POST",
            "/api/v1/agent-tokens",
            json=request.model_dump(exclude_none=True),
        )
        return AgentTokenCreated.model_validate(body)

    async def list_agent_tokens(self) -> list[AgentTokenSummary]:
        """List tokens for the caller's tenant (newest first).

        Returns the summary shape only -- the raw secret is never
        round-tripped on this endpoint.
        """
        body = await self._request("GET", "/api/v1/agent-tokens")
        return [AgentTokenSummary.model_validate(row) for row in body]

    async def revoke_agent_token(self, token_id: str) -> None:
        """Revoke a token by id.

        Idempotent -- revoking an already-revoked token is a no-op
        and still returns 204. Unknown ``token_id`` raises
        ``CanonAPIError(code='resource_not_found')``.
        """
        await self._request("DELETE", f"/api/v1/agent-tokens/{token_id}")

    # ------------------------------------------------------------------
    # Internal: request + error mapping
    # ------------------------------------------------------------------

    async def _sse(
        self,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        method: str = "GET",
    ) -> AsyncIterator[IngestJobEvent]:
        """Open a Server-Sent Events stream and yield parsed frames.

        Each frame is emitted as :class:`IngestJobEvent` regardless of
        the source endpoint -- the ``event`` field carries the SSE
        event type (``stage`` / ``token`` / ``complete`` / ``failed`` /
        ...) and ``data`` carries the parsed JSON payload. ``cursor``
        is populated from ``data.cursor`` when present (job streams) or
        a monotonic counter otherwise (query streams).
        """
        cleaned_params: dict[str, str] = {k: str(v) for k, v in (params or {}).items() if v not in (None, "")}
        cursor_counter = 0
        try:
            async with self._client.stream(
                method,
                path,
                json=json,
                params=cleaned_params,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 300:
                    await response.aread()
                    await _raise_for_problem(response)
                event_type = "message"
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        # Blank line terminates a frame.
                        if data_lines:
                            raw = "\n".join(data_lines)
                            try:
                                payload = _jsonlib.loads(raw)
                            except _jsonlib.JSONDecodeError:
                                payload = {"raw": raw}
                            cursor = (
                                int(payload["cursor"])
                                if isinstance(payload, dict) and "cursor" in payload
                                else cursor_counter
                            )
                            cursor_counter += 1
                            yield IngestJobEvent(
                                cursor=cursor,
                                event=event_type,
                                data=payload if isinstance(payload, dict) else {"data": payload},
                            )
                        event_type = "message"
                        data_lines = []
                        continue
                    if line.startswith(":"):
                        # Comment / heartbeat -- ignore.
                        continue
                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].lstrip())
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise CanonConnectionError(str(exc)) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, str | None] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        cleaned_params: dict[str, str] = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        try:
            response = await self._client.request(
                method,
                path,
                json=json,
                params=cleaned_params,
                headers=headers,
            )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise CanonConnectionError(str(exc)) from exc
        if 200 <= response.status_code < 300:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        await _raise_for_problem(response)


class AgentSurface:
    """Agent-tier sub-client -- ``client.agent.<method>``.

    Mirrors the 8 routes under ``/api/v1/agent/*``. Every POST
    requires an explicit ``idempotency_key`` argument (service-side
    requirement; the SDK validates the value is non-empty before the
    request goes out so callers get a fast local error). Agent-tier
    calls typically authenticate with ``X-Agent-Token`` -- set the
    token on the parent :class:`CanonClient` (constructor kwarg
    ``agent_token=...``).
    """

    def __init__(self, parent: CanonClient) -> None:
        self._parent = parent

    # -- Sources --------------------------------------------------------

    async def ingest_source(
        self,
        spec: SubmitSourceJsonPayload,
        *,
        idempotency_key: str,
    ) -> SourceRecord:
        """Submit a source for intake (agent tier).

        Scope: ``agent.sources:ingest``. ``idempotency_key`` is
        mandatory on the wire (``400 missing_idempotency_key`` on
        the service if absent) -- the SDK rejects an empty value
        locally to surface the requirement before the round-trip.
        """
        _require_idempotency_key(idempotency_key)
        body = await self._parent._request(
            "POST",
            "/api/v1/agent/sources",
            json=spec.model_dump(exclude_none=True),
            headers={_HEADER_IDEMPOTENCY_KEY: idempotency_key},
        )
        return SourceRecord.model_validate(body)

    async def get_source(self, source_id: str) -> SourceRecord:
        """Read a single source record (agent tier).

        Scope: ``agent.sources:read``.
        """
        body = await self._parent._request("GET", f"/api/v1/agent/sources/{source_id}")
        return SourceRecord.model_validate(body)

    # -- Query ----------------------------------------------------------

    async def query(
        self,
        request: AnswerRequest,
        *,
        idempotency_key: str,
    ) -> AnswerResponse:
        """Grounded RAG answer with explicit citations (agent tier).

        Scope: ``agent.query:run``. Mandatory ``idempotency_key``.
        Identical wire shape to the user-tier ``POST /api/v1/query``;
        the only differences are the auth gate and the required
        idempotency key.
        """
        _require_idempotency_key(idempotency_key)
        body = await self._parent._request(
            "POST",
            "/api/v1/agent/query",
            json=request.model_dump(exclude_none=True),
            headers={_HEADER_IDEMPOTENCY_KEY: idempotency_key},
        )
        return AnswerResponse.model_validate(body)

    def query_stream(
        self,
        request: AnswerRequest,
        *,
        idempotency_key: str,
    ) -> AsyncIterator[IngestJobEvent]:
        """Stream the agent answer endpoint as Server-Sent Events.

        Scope: ``agent.query:run``. Mandatory ``idempotency_key``.
        Two frame types on the wire: ``hit`` (one per retrieved
        citation) and ``final`` (terminal frame with the full
        answer). Each yielded frame uses the generic SDK shape
        :class:`IngestJobEvent` regardless of event type.
        """
        _require_idempotency_key(idempotency_key)

        async def _gen() -> AsyncIterator[IngestJobEvent]:
            cleaned_params: dict[str, str] = {}
            cursor_counter = 0
            try:
                async with self._parent._client.stream(
                    "POST",
                    "/api/v1/agent/query/stream",
                    json=request.model_dump(exclude_none=True),
                    params=cleaned_params,
                    headers={
                        "Accept": "text/event-stream",
                        _HEADER_IDEMPOTENCY_KEY: idempotency_key,
                    },
                ) as response:
                    if response.status_code >= 300:
                        await response.aread()
                        await _raise_for_problem(response)
                    event_type = "message"
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if line == "":
                            if data_lines:
                                raw = "\n".join(data_lines)
                                try:
                                    payload = _jsonlib.loads(raw)
                                except _jsonlib.JSONDecodeError:
                                    payload = {"raw": raw}
                                yield IngestJobEvent(
                                    cursor=cursor_counter,
                                    event=event_type,
                                    data=payload if isinstance(payload, dict) else {"data": payload},
                                )
                                cursor_counter += 1
                            event_type = "message"
                            data_lines = []
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_type = line[len("event:") :].strip() or "message"
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:") :].lstrip())
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                raise CanonConnectionError(str(exc)) from exc

        return _gen()

    async def search(
        self,
        request: SearchRequest,
        *,
        idempotency_key: str,
    ) -> SearchResponse:
        """Hybrid retrieval over the canon corpus (agent tier).

        Scope: ``agent.query:run``. Mandatory ``idempotency_key``.
        No LLM call -- just BM25 + dense retrieval fused via RRF.
        """
        _require_idempotency_key(idempotency_key)
        body = await self._parent._request(
            "POST",
            "/api/v1/agent/search",
            json=request.model_dump(exclude_none=True),
            headers={_HEADER_IDEMPOTENCY_KEY: idempotency_key},
        )
        return SearchResponse.model_validate(body)

    # -- Knowledge ------------------------------------------------------

    async def get_knowledge(self, item_id: str) -> KnowledgeItem:
        """Fetch a single knowledge item by id (agent tier).

        Scope: ``agent.knowledge:read``. Returns the pointer view
        (current version metadata), not the version body.
        """
        body = await self._parent._request("GET", f"/api/v1/agent/knowledge/{item_id}")
        return KnowledgeItem.model_validate(body)

    async def get_provenance(
        self,
        item_id: str,
        *,
        version: int | None = None,
    ) -> Provenance:
        """Resolve the citation graph for ``(item_id, version)``.

        Scope: ``agent.knowledge:read``. ``version=None`` resolves to
        the current version, matching the user-tier surface.
        """
        body = await self._parent._request(
            "GET",
            f"/api/v1/agent/knowledge/{item_id}/provenance",
            params={"version": str(version)} if version else None,
        )
        return Provenance.model_validate(body)

    # -- Candidates -----------------------------------------------------

    async def propose_candidates(
        self,
        request: ProposeCandidateRequest,
        *,
        idempotency_key: str,
    ) -> list[CandidateRecord]:
        """Propose candidates from an existing source (agent tier).

        Scope: ``agent.candidates:propose``. Mandatory
        ``idempotency_key``. Returns the persisted candidate list
        (status ``proposed`` -- a human still adjudicates).
        """
        _require_idempotency_key(idempotency_key)
        body = await self._parent._request(
            "POST",
            "/api/v1/agent/candidates:propose",
            json=request.model_dump(exclude_none=True),
            headers={_HEADER_IDEMPOTENCY_KEY: idempotency_key},
        )
        return [CandidateRecord.model_validate(row) for row in body]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _require_idempotency_key(value: str | None) -> None:
    """Reject empty or whitespace-only idempotency keys early.

    Agent-tier POSTs MUST carry an ``Idempotency-Key`` header (the
    service returns ``400 missing_idempotency_key`` otherwise). The
    SDK enforces the same shape locally so callers see a
    :class:`ValueError` before the round-trip when they forget to
    supply one.
    """
    if not value or not value.strip():
        raise ValueError("idempotency_key is required for agent-tier POSTs and must be a non-empty string")


async def _raise_for_problem(response: httpx.Response) -> None:
    """Translate a non-2xx response into the right SDK exception.

    Parses the RFC 7807 envelope and dispatches by ``code`` -- known
    codes (``invalid_agent_token``, ``missing_idempotency_key``,
    ``invalid_request``, ...) get their typed subclass; everything
    else falls back to :class:`CanonAPIError`. The ``errors`` array
    on the envelope is parsed into :class:`FieldError` instances on
    every exception (most useful on :class:`ValidationError`).
    """
    try:
        payload: dict[str, Any] | str = response.json()
    except Exception:
        payload = response.text or ""
    if isinstance(payload, dict):
        code = str(payload.get("code") or "http_error")
        field_errors = _parse_field_errors(payload.get("errors"))
        exc_class = _resolve_api_error_class(code)
        raise exc_class(
            status_code=int(payload.get("status") or response.status_code),
            code=code,
            title=str(payload.get("title") or f"HTTP {response.status_code}"),
            detail=payload.get("detail"),
            extensions=payload.get("extensions"),
            payload=payload,
            errors=field_errors,
        )
    raise CanonAPIError(
        status_code=response.status_code,
        code="http_error",
        title=f"HTTP {response.status_code}",
        detail=str(payload) or None,
        extensions=None,
        payload=payload,
    )


def _csv(value: Iterable[str] | None) -> str | None:
    if not value:
        return None
    return ",".join(value)


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value
