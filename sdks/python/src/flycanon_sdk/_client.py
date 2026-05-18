# Copyright 2026 Firefly Software Solutions Inc
"""``CanonClient`` -- async client for every flycanon endpoint.

Pooled httpx.AsyncClient under the hood. Use as an async context
manager to get clean connection lifecycle:

    async with CanonClient(base_url="http://localhost:8500") as client:
        ...
"""

from __future__ import annotations

from collections.abc import Iterable
from types import TracebackType
from typing import Any

import httpx

from flycanon_sdk._errors import CanonAPIError, CanonConnectionError
from flycanon_sdk._models import (
    AcceptCandidateRequest,
    AnswerRequest,
    AnswerResponse,
    AuditPage,
    CandidateRecord,
    CandidatesPage,
    CreateKnowledgeRequest,
    CreateTaxonomyNodeRequest,
    KnowledgeItem,
    KnowledgeItemsPage,
    KnowledgeVersion,
    ProposeCandidateRequest,
    Provenance,
    RejectCandidateRequest,
    RetireKnowledgeRequest,
    SearchRequest,
    SearchResponse,
    SourceRecord,
    SourcesPage,
    SubmitSourceJsonPayload,
    SupersedeKnowledgeRequest,
    TaxonomyNode,
    TaxonomyTree,
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
            "User-Agent": "flycanon-sdk-python/26.5.1",
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

    async def __aenter__(self) -> "CanonClient":
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

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    async def create_knowledge(self, request: CreateKnowledgeRequest) -> KnowledgeVersion:
        body = await self._request(
            "POST", "/api/v1/knowledge", json=request.model_dump(exclude_none=True)
        )
        return KnowledgeVersion.model_validate(body)

    async def update_knowledge(
        self, item_id: str, request: UpdateKnowledgeRequest
    ) -> KnowledgeVersion:
        body = await self._request(
            "PUT",
            f"/api/v1/knowledge/{item_id}",
            json=request.model_dump(exclude_none=True),
        )
        return KnowledgeVersion.model_validate(body)

    async def supersede_knowledge(
        self, item_id: str, request: SupersedeKnowledgeRequest
    ) -> KnowledgeItem:
        body = await self._request(
            "POST",
            f"/api/v1/knowledge/{item_id}:supersede",
            json=request.model_dump(exclude_none=True),
        )
        return KnowledgeItem.model_validate(body)

    async def retire_knowledge(
        self, item_id: str, request: RetireKnowledgeRequest
    ) -> KnowledgeItem:
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

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    async def propose_candidates(
        self, request: ProposeCandidateRequest
    ) -> list[CandidateRecord]:
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

    async def accept_candidate(
        self, candidate_id: str, request: AcceptCandidateRequest
    ) -> CandidateRecord:
        body = await self._request(
            "POST",
            f"/api/v1/candidates/{candidate_id}:accept",
            json=request.model_dump(exclude_none=True),
        )
        return CandidateRecord.model_validate(body)

    async def reject_candidate(
        self, candidate_id: str, request: RejectCandidateRequest
    ) -> CandidateRecord:
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
        body = await self._request(
            "POST", "/api/v1/search", json=request.model_dump(exclude_none=True)
        )
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
        body = await self._request(
            "POST", "/api/v1/query", json=request.model_dump(exclude_none=True)
        )
        return AnswerResponse.model_validate(body)

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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, str | None] | None = None,
    ) -> Any:
        cleaned_params: dict[str, str] = {
            k: v for k, v in (params or {}).items() if v not in (None, "")
        }
        try:
            response = await self._client.request(
                method, path, json=json, params=cleaned_params
            )
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
