# Copyright 2026 Firefly Software Solutions Inc
"""``CanonClient`` -- async client for every flycanon endpoint.

Pooled httpx.AsyncClient under the hood. Use as an async context
manager to get clean connection lifecycle:

    async with CanonClient(base_url="http://localhost:8500") as client:
        ...
"""

from __future__ import annotations

import json as _jsonlib
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from types import TracebackType
from typing import Any

import httpx

from flycanon_sdk._errors import CanonAPIError, CanonConnectionError
from flycanon_sdk._models import (
    AcceptCandidateRequest,
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
)


class CanonClient:
    """Async HTTP client for the flycanon service."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        merged_headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "flycanon-sdk-python/26.5.4",
        }
        if api_key:
            merged_headers["Authorization"] = f"Bearer {api_key}"
        if headers:
            merged_headers.update(headers)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=merged_headers,
        )

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
    # Async ingest jobs
    # ------------------------------------------------------------------

    async def get_job(self, job_id: str) -> IngestJob:
        body = await self._request("GET", f"/api/v1/jobs/{job_id}")
        return IngestJob.model_validate(body)

    async def cancel_job(self, job_id: str) -> IngestJob:
        body = await self._request("POST", f"/api/v1/jobs/{job_id}:cancel")
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
        return self._sse(f"/api/v1/jobs/{job_id}/stream", params={"cursor": cursor})

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
    ) -> Any:
        cleaned_params: dict[str, str] = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        try:
            response = await self._client.request(method, path, json=json, params=cleaned_params)
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise CanonConnectionError(str(exc)) from exc
        if 200 <= response.status_code < 300:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        await _raise_for_problem(response)


async def _raise_for_problem(response: httpx.Response) -> None:
    try:
        payload: dict[str, Any] | str = response.json()
    except Exception:
        payload = response.text or ""
    if isinstance(payload, dict):
        raise CanonAPIError(
            status_code=int(payload.get("status") or response.status_code),
            code=str(payload.get("code") or "http_error"),
            title=str(payload.get("title") or f"HTTP {response.status_code}"),
            detail=payload.get("detail"),
            extensions=payload.get("extensions"),
            payload=payload,
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
