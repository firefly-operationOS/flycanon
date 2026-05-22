# Copyright 2026 Firefly Software Solutions Inc
"""Async client smoke tests against a respx-mocked transport."""

from __future__ import annotations

import pytest
import respx

from flycanon_sdk import (
    CANON_WORKSPACES_TOPIC,
    AgentCannotMint,
    AgentScopeDenied,
    AgentTokenCreated,
    AgentTokenExpired,
    AgentTokenMintRequest,
    AgentTokenSummary,
    AgentWorkspaceNotInAllowlist,
    AnswerRequest,
    AnswerResponse,
    BillingReport,
    BillingSummary,
    CanonAPIError,
    CanonClient,
    ConflictScanResponse,
    CorpusStats,
    CostEventsPage,
    CreateConversationTurnRequest,
    CreateRelationRequest,
    IngestJob,
    InvalidAgentToken,
    KnowledgeDiff,
    KnowledgeGraph,
    KnowledgeItem,
    LatencyReport,
    MissingAgentToken,
    MissingIdempotencyKey,
    ProposeCandidateRequest,
    Provenance,
    RelationsList,
    SearchRequest,
    SearchResponse,
    SourceMetadata,
    SourceRecord,
    StaleReport,
    SubjectCostReport,
    SubmitSourceJsonPayload,
    SuggestionsResponse,
    TopConsumersReport,
    ValidationError,
    VersionInfo,
    WorkspaceCreate,
    WorkspaceCreated,
    WorkspaceDeleted,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
    WorkspaceUpdated,
    __version__,
)


@pytest.mark.asyncio
async def test_version_returns_version_info() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/version").respond(
            json={
                "service": "flycanon",
                "version": "26.5.4",
                "embedding_model": "openai:text-embedding-3-small",
                "answer_model": "anthropic:claude-sonnet-4-6",
                "answer_fallback_model": "openai:gpt-4o",
                "vector_store": "sqlite-vec",
                "eda_adapter": "postgres",
            }
        )
        info = await client.version()
        assert isinstance(info, VersionInfo)
        assert info.service == "flycanon"


@pytest.mark.asyncio
async def test_submit_source_round_trips_payload() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        route = mock.post("/api/v1/sources").respond(
            201,
            json={
                "id": "src-1",
                "kind": "docx",
                "status": "ingested",
                "content_sha256": "deadbeef",
                "content_bytes": 1024,
                "n_chunks": 7,
                "metadata": {"title": "Sample"},
                "created_at": "2026-05-18T17:00:00Z",
                "updated_at": "2026-05-18T17:00:01Z",
            },
        )
        record = await client.submit_source(
            SubmitSourceJsonPayload(
                content_base64="aGVsbG8=",
                filename="hello.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                metadata=SourceMetadata(title="Sample"),
            )
        )
        assert record.id == "src-1"
        assert record.n_chunks == 7
        assert route.called


@pytest.mark.asyncio
async def test_api_error_maps_problem_details() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/knowledge/missing").respond(
            404,
            json={
                "type": "https://flycanon.dev/problems/knowledge-item-not-found",
                "title": "Knowledge item not found",
                "status": 404,
                "code": "knowledge_item_not_found",
                "detail": "knowledge item 'missing' not found",
                "extensions": {"item_id": "missing"},
            },
        )
        with pytest.raises(CanonAPIError) as excinfo:
            await client.get_knowledge("missing")
        err = excinfo.value
        assert err.code == "knowledge_item_not_found"
        assert err.status_code == 404
        assert err.extensions["item_id"] == "missing"


@pytest.mark.asyncio
async def test_submit_source_async_returns_job() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/sources:async").respond(
            202,
            json={
                "id": "job-1",
                "status": "queued",
                "progress": 0.0,
                "created_at": "2026-05-18T17:00:00Z",
                "updated_at": "2026-05-18T17:00:00Z",
            },
        )
        job = await client.submit_source_async(
            SubmitSourceJsonPayload(content_base64="aGk=", filename="hi.md")
        )
        assert isinstance(job, IngestJob)
        assert job.status == "queued"


@pytest.mark.asyncio
async def test_get_diff_round_trips() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/knowledge/ki-1/diff").respond(
            json={
                "knowledge_item_id": "ki-1",
                "from_version": 1,
                "to_version": 2,
                "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
                "field_changes": [{"field": "title", "from": "Old", "to": "New"}],
                "added_citations": [],
                "removed_citations": [],
            }
        )
        diff = await client.get_diff("ki-1", from_version=1, to_version=2)
        assert isinstance(diff, KnowledgeDiff)
        assert diff.field_changes[0].field == "title"


@pytest.mark.asyncio
async def test_list_relations_returns_outgoing_incoming() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/knowledge/ki-1/relations").respond(
            json={
                "outgoing": [
                    {
                        "id": "rel-1",
                        "from_item_id": "ki-1",
                        "to_item_id": "ki-2",
                        "kind": "depends_on",
                        "created_at": "2026-05-18T17:00:00Z",
                    }
                ],
                "incoming": [],
            }
        )
        rels = await client.list_relations("ki-1")
        assert isinstance(rels, RelationsList)
        assert rels.outgoing[0].kind == "depends_on"


@pytest.mark.asyncio
async def test_add_relation_posts_request_body() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        route = mock.post("/api/v1/knowledge/ki-1/relations").respond(
            201,
            json={
                "id": "rel-1",
                "from_item_id": "ki-1",
                "to_item_id": "ki-2",
                "kind": "conflicts_with",
                "created_at": "2026-05-18T17:00:00Z",
            },
        )
        rel = await client.add_relation(
            "ki-1",
            CreateRelationRequest(to_item_id="ki-2", kind="conflicts_with"),
        )
        assert rel.kind == "conflicts_with"
        assert route.called


@pytest.mark.asyncio
async def test_get_graph_returns_typed_graph() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/knowledge:graph").respond(
            json={
                "nodes": [
                    {"id": "n1", "label": "A", "kind": "knowledge_item"},
                    {"id": "n2", "label": "B", "kind": "knowledge_item"},
                ],
                "edges": [{"from": "n1", "to": "n2", "kind": "related"}],
            }
        )
        graph = await client.get_graph(include_sources=False)
        assert isinstance(graph, KnowledgeGraph)
        assert graph.edges[0].kind == "related"
        assert graph.edges[0].from_id == "n1"


@pytest.mark.asyncio
async def test_scan_stale_returns_report() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/knowledge:stale").respond(
            json={
                "items": [
                    {
                        "knowledge_item_id": "ki-1",
                        "title": "X",
                        "domain": "process",
                        "score": 0.5,
                        "max_similarity": 0.5,
                        "sample_size": 3,
                        "computed_at": "2026-05-18T17:00:00Z",
                    }
                ],
                "total": 1,
            }
        )
        report = await client.scan_stale()
        assert isinstance(report, StaleReport)
        assert report.total == 1


@pytest.mark.asyncio
async def test_detect_conflicts_returns_relation_ids() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/knowledge:detect-conflicts").respond(
            json={
                "pairs_evaluated": 4,
                "conflicts_found": 1,
                "candidate_ids": ["cand-1"],
                "relation_ids": ["rel-1"],
            }
        )
        response = await client.detect_conflicts()
        assert isinstance(response, ConflictScanResponse)
        assert response.relation_ids == ["rel-1"]


@pytest.mark.asyncio
async def test_conversation_add_turn_returns_citations() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/conversations/c-1/turns").respond(
            json={
                "id": "trn-1",
                "conversation_id": "c-1",
                "query": "...",
                "answer": "yes",
                "citations": [],
                "model": "anthropic:claude-sonnet-4-6",
                "created_at": "2026-05-18T17:00:00Z",
            }
        )
        turn = await client.add_turn("c-1", CreateConversationTurnRequest(query="what?"))
        assert turn.answer == "yes"


@pytest.mark.asyncio
async def test_suggest_questions_returns_list() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/conversations/c-1/suggest").respond(json={"questions": ["What about X?", "Y?"]})
        suggestions = await client.suggest_questions("c-1")
        assert isinstance(suggestions, SuggestionsResponse)
        assert len(suggestions.questions) == 2


@pytest.mark.asyncio
async def test_billing_report_returns_rows() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/billing").respond(
            json={
                "rows": [
                    {
                        "group": {"date": "2026-05-18", "model": "x"},
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                        "cost_usd": "0.01",
                        "calls": 1,
                    }
                ],
                "total_cost_usd": "0.01",
                "total_calls": 1,
            }
        )
        report = await client.billing_report(group_by=["date", "model"])
        assert isinstance(report, BillingReport)
        assert report.total_calls == 1


@pytest.mark.asyncio
async def test_list_cost_events_returns_page() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/billing/events").respond(
            json={
                "rows": [
                    {
                        "id": 1,
                        "agent_name": "flycanon-answerer",
                        "model": "anthropic:claude-sonnet-4-6",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                        "cost_usd": "0.01",
                        "latency_ms": 420,
                        "occurred_at": "2026-05-18T17:00:00Z",
                    }
                ],
                "limit": 50,
                "offset": 0,
            }
        )
        page = await client.list_cost_events()
        assert isinstance(page, CostEventsPage)
        assert page.rows[0].latency_ms == 420


@pytest.mark.asyncio
async def test_billing_summary_returns_three_windows() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        window = {
            "since": "2026-05-17T17:00:00Z",
            "calls": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost_usd": "0.01",
            "top_model": "x",
            "top_model_cost_usd": "0.01",
            "top_actor": None,
            "top_actor_cost_usd": "0",
        }
        mock.get("/api/v1/billing/summary").respond(
            json={
                "generated_at": "2026-05-18T17:00:00Z",
                "last_24h": window,
                "last_7d": window,
                "last_30d": window,
            }
        )
        summary = await client.billing_summary()
        assert isinstance(summary, BillingSummary)
        assert summary.last_24h.calls == 1


@pytest.mark.asyncio
async def test_billing_top_validates_dimension() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/billing/top").respond(
            json={
                "dimension": "actor",
                "rows": [
                    {
                        "dimension": "actor",
                        "value": "alice",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                        "cost_usd": "0.05",
                        "calls": 3,
                    }
                ],
            }
        )
        top = await client.billing_top(dimension="actor")
        assert isinstance(top, TopConsumersReport)
        assert top.rows[0].value == "alice"


@pytest.mark.asyncio
async def test_billing_by_subject_returns_rows() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/billing/by-subject").respond(
            json={
                "rows": [
                    {
                        "subject_kind": "source",
                        "subject_id": "src-1",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                        "cost_usd": "0.05",
                        "calls": 3,
                    }
                ]
            }
        )
        report = await client.billing_by_subject()
        assert isinstance(report, SubjectCostReport)
        assert report.rows[0].subject_id == "src-1"


@pytest.mark.asyncio
async def test_billing_latency_returns_percentiles() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/billing/latency").respond(
            json={
                "rows": [
                    {
                        "group": {"model": "x"},
                        "count": 10,
                        "avg_ms": 500,
                        "p50_ms": 480,
                        "p95_ms": 920,
                        "p99_ms": 1240,
                        "max_ms": 1500,
                    }
                ]
            }
        )
        report = await client.billing_latency(group_by=["model"])
        assert isinstance(report, LatencyReport)
        assert report.rows[0].p95_ms == 920


@pytest.mark.asyncio
async def test_stats_returns_full_snapshot() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/stats").respond(
            json={
                "generated_at": "2026-05-18T17:00:00Z",
                "sources": {
                    "total": 1,
                    "by_kind": {"pdf": 1},
                    "by_status": {"ingested": 1},
                    "total_bytes": 1024,
                },
                "knowledge_items": {
                    "total": 0,
                    "by_status": {},
                    "by_domain": {},
                },
                "knowledge_versions": 0,
                "candidates": {"total": 0, "by_status": {}},
                "chunks": {"total": 0, "embedded": 0, "embedded_pct": 0.0},
                "ingest_jobs": {"total": 0, "by_status": {}, "avg_attempts": 0.0},
                "cost": {
                    "total_events": 0,
                    "cost_usd_24h": "0",
                    "cost_usd_30d": "0",
                },
            }
        )
        snap = await client.stats()
        assert isinstance(snap, CorpusStats)
        assert snap.sources.total == 1


@pytest.mark.asyncio
async def test_answer_returns_typed_response() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/query").respond(
            json={
                "answer": "Yes.",
                "citations": [
                    {
                        "chunk_id": "c-1",
                        "source_id": "s-1",
                        "content": "...",
                        "score": 0.91,
                        "metadata": {},
                    }
                ],
                "model": "anthropic:claude-sonnet-4-6",
                "elapsed_ms": 720,
                "no_answer": False,
            }
        )
        answer = await client.answer("Is the sky blue?")
        assert isinstance(answer, AnswerResponse)
        assert answer.citations[0].chunk_id == "c-1"


# ----------------------------------------------------------------------
# 26.6 unification -- version + module surface
# ----------------------------------------------------------------------


def test_sdk_version_string_is_2660() -> None:
    assert __version__ == "26.6.0"


def test_canon_workspaces_topic_constant() -> None:
    assert CANON_WORKSPACES_TOPIC == "canon.workspaces.v1"


# ----------------------------------------------------------------------
# Header injection -- tenant / workspace / correlation / agent token
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_constructor_headers_attached_on_every_request() -> None:
    """Every outbound request carries the four canonical headers."""
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon",
        tenant_id="tenant-acme",
        workspace_id="ws-1",
        correlation_id="corr-42",
        agent_token="canon_abc123",
    ) as client:
        route = mock.get("/api/v1/version").respond(
            json={
                "service": "flycanon",
                "version": "26.6.0",
                "embedding_model": "openai:text-embedding-3-small",
                "answer_model": "anthropic:claude-sonnet-4-6",
                "answer_fallback_model": "openai:gpt-4o",
                "vector_store": "sqlite-vec",
                "eda_adapter": "postgres",
            }
        )
        await client.version()
        request = route.calls.last.request
        assert request.headers["X-Tenant-Id"] == "tenant-acme"
        assert request.headers["X-Workspace-Id"] == "ws-1"
        assert request.headers["X-Correlation-Id"] == "corr-42"
        assert request.headers["X-Agent-Token"] == "canon_abc123"


@pytest.mark.asyncio
async def test_constructor_headers_default_to_unset() -> None:
    """Without the new kwargs the request carries no firefly headers."""
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        route = mock.get("/api/v1/version").respond(
            json={
                "service": "flycanon",
                "version": "26.6.0",
                "embedding_model": "openai:text-embedding-3-small",
                "answer_model": "anthropic:claude-sonnet-4-6",
                "answer_fallback_model": "openai:gpt-4o",
                "vector_store": "sqlite-vec",
                "eda_adapter": "postgres",
            }
        )
        await client.version()
        request = route.calls.last.request
        assert "X-Tenant-Id" not in request.headers
        assert "X-Workspace-Id" not in request.headers
        assert "X-Correlation-Id" not in request.headers
        assert "X-Agent-Token" not in request.headers


@pytest.mark.asyncio
async def test_user_agent_advertises_2660() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        route = mock.get("/api/v1/version").respond(
            json={
                "service": "flycanon",
                "version": "26.6.0",
                "embedding_model": "x",
                "answer_model": "y",
                "answer_fallback_model": "z",
                "vector_store": "sqlite-vec",
                "eda_adapter": "postgres",
            }
        )
        await client.version()
        assert "flycanon-sdk-python/26.6.0" in route.calls.last.request.headers["User-Agent"]


# ----------------------------------------------------------------------
# /api/v1/ingest-jobs path rename (was /api/v1/jobs)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_uses_ingest_jobs_path() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        route = mock.get("/api/v1/ingest-jobs/job-7").respond(
            json={
                "id": "job-7",
                "status": "succeeded",
                "progress": 1.0,
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:01Z",
            }
        )
        job = await client.get_job("job-7")
        assert isinstance(job, IngestJob)
        assert route.called


@pytest.mark.asyncio
async def test_cancel_job_uses_ingest_jobs_path() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        route = mock.post("/api/v1/ingest-jobs/job-7:cancel").respond(
            json={
                "id": "job-7",
                "status": "cancelled",
                "progress": 0.5,
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:01Z",
            }
        )
        await client.cancel_job("job-7")
        assert route.called


# ----------------------------------------------------------------------
# Typed exception classes keyed by RFC 7807 ``code``
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_idempotency_key_problem_yields_typed_exception() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/agent/sources").respond(
            400,
            json={
                "type": "https://flycanon.dev/problems/missing-idempotency-key",
                "title": "Idempotency-Key header required",
                "status": 400,
                "code": "missing_idempotency_key",
                "detail": "Idempotency-Key header is required for /api/v1/agent/* POST routes.",
            },
        )
        with pytest.raises(MissingIdempotencyKey) as excinfo:
            await client.agent.ingest_source(
                SubmitSourceJsonPayload(content_base64="aGk=", filename="hi.md"),
                idempotency_key="key-1",
            )
        assert excinfo.value.code == "missing_idempotency_key"
        assert excinfo.value.status_code == 400
        # Subclasses are CanonAPIError -- callers can still catch the
        # generic class if they don't care about the specific code.
        assert isinstance(excinfo.value, CanonAPIError)


@pytest.mark.asyncio
async def test_missing_agent_token_problem_yields_typed_exception() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/agent/sources/src-1").respond(
            401,
            json={
                "type": "https://flycanon.dev/problems/missing-agent-token",
                "title": "X-Agent-Token header required",
                "status": 401,
                "code": "missing_agent_token",
                "detail": "X-Agent-Token header is required for /api/v1/agent/* routes.",
            },
        )
        with pytest.raises(MissingAgentToken) as excinfo:
            await client.agent.get_source("src-1")
        assert excinfo.value.code == "missing_agent_token"
        assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_agent_token_problem_yields_typed_exception() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/agent/sources/src-1").respond(
            403,
            json={
                "type": "https://flycanon.dev/problems/invalid-agent-token",
                "title": "Invalid agent token",
                "status": 403,
                "code": "invalid_agent_token",
                "detail": "Token shape is malformed.",
            },
        )
        with pytest.raises(InvalidAgentToken) as excinfo:
            await client.agent.get_source("src-1")
        assert excinfo.value.code == "invalid_agent_token"


@pytest.mark.asyncio
async def test_agent_token_expired_problem_yields_typed_exception() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/agent/sources/src-1").respond(
            403,
            json={
                "type": "https://flycanon.dev/problems/agent-token-expired",
                "title": "Agent token expired",
                "status": 403,
                "code": "agent_token_expired",
                "detail": "Token expires_at is in the past.",
            },
        )
        with pytest.raises(AgentTokenExpired) as excinfo:
            await client.agent.get_source("src-1")
        assert excinfo.value.code == "agent_token_expired"


@pytest.mark.asyncio
async def test_agent_workspace_not_in_allowlist_problem() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/agent/sources/src-1").respond(
            403,
            json={
                "type": "https://flycanon.dev/problems/agent-workspace-not-in-allowlist",
                "title": "Workspace not in allowlist",
                "status": 403,
                "code": "agent_workspace_not_in_allowlist",
                "detail": "Token allowlist does not include workspace ws-1.",
            },
        )
        with pytest.raises(AgentWorkspaceNotInAllowlist) as excinfo:
            await client.agent.get_source("src-1")
        assert excinfo.value.code == "agent_workspace_not_in_allowlist"


@pytest.mark.asyncio
async def test_agent_scope_denied_problem() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/agent/sources/src-1").respond(
            403,
            json={
                "type": "https://flycanon.dev/problems/agent-scope-denied",
                "title": "Scope denied",
                "status": 403,
                "code": "agent_scope_denied",
                "detail": "Token scopes do not include agent.sources:read.",
            },
        )
        with pytest.raises(AgentScopeDenied) as excinfo:
            await client.agent.get_source("src-1")
        assert excinfo.value.code == "agent_scope_denied"


@pytest.mark.asyncio
async def test_agent_cannot_mint_problem() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/agent-tokens").respond(
            403,
            json={
                "type": "https://flycanon.dev/problems/agent-cannot-mint",
                "title": "Agent cannot mint tokens",
                "status": 403,
                "code": "agent_cannot_mint",
                "detail": "Agent-tier callers cannot manage agent tokens.",
            },
        )
        with pytest.raises(AgentCannotMint):
            await client.mint_agent_token(AgentTokenMintRequest(name="harvester"))


@pytest.mark.asyncio
async def test_validation_error_parses_errors_array() -> None:
    """``invalid_request`` ProblemDetail with ``errors[]`` populated
    surfaces typed :class:`FieldError` triples on the SDK exception.
    """
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.post("/api/v1/workspaces").respond(
            400,
            json={
                "type": "https://flycanon.dev/problems/invalid-request",
                "title": "Request validation failed.",
                "status": 400,
                "code": "invalid_request",
                "detail": "Request validation failed.",
                "errors": [
                    {"code": "string_too_short", "path": "name", "message": "name too short"},
                    {"code": "missing", "path": "id", "message": "id is required"},
                ],
            },
        )
        with pytest.raises(ValidationError) as excinfo:
            # Locally valid DTO so we reach the mocked transport;
            # the simulated server then returns the invalid_request
            # envelope with the structured per-field errors.
            await client.create_workspace(WorkspaceCreate(id="ws-bad", name="bad"))
        err = excinfo.value
        assert err.code == "invalid_request"
        assert len(err.errors) == 2
        assert err.errors[0].code == "string_too_short"
        assert err.errors[0].path == "name"
        assert err.errors[1].code == "missing"
        assert err.errors[1].path == "id"


@pytest.mark.asyncio
async def test_unknown_code_falls_back_to_canon_api_error() -> None:
    """Codes the SDK doesn't know about land on the base
    :class:`CanonAPIError` so newer services keep working."""
    async with respx.mock(base_url="http://canon") as mock, CanonClient(base_url="http://canon") as client:
        mock.get("/api/v1/knowledge/missing").respond(
            404,
            json={
                "type": "https://flycanon.dev/problems/some-future-code",
                "title": "Some future error",
                "status": 404,
                "code": "future_unknown_code",
            },
        )
        with pytest.raises(CanonAPIError) as excinfo:
            await client.get_knowledge("missing")
        # Direct CanonAPIError, not a subclass.
        assert excinfo.value.__class__ is CanonAPIError
        assert excinfo.value.code == "future_unknown_code"


# ----------------------------------------------------------------------
# Workspace CRUD
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workspace_posts_to_workspaces_path() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon",
        tenant_id="acme",
        workspace_id="ws-1",
    ) as client:
        route = mock.post("/api/v1/workspaces").respond(
            201,
            json={
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Workspace 1",
                "status": "active",
                "scope": None,
                "sme_roster": None,
                "retention_days": None,
                "jurisdiction": None,
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:00Z",
                "closed_at": None,
            },
        )
        spec = await client.create_workspace(WorkspaceCreate(id="ws-1", name="Workspace 1"))
        assert isinstance(spec, WorkspaceSpec)
        assert spec.id == "ws-1"
        assert spec.tenant_id == "acme"
        assert route.called
        # Tenant header surfaced from the constructor.
        assert route.calls.last.request.headers["X-Tenant-Id"] == "acme"


@pytest.mark.asyncio
async def test_list_workspaces_returns_summaries() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        mock.get("/api/v1/workspaces").respond(
            json=[
                {
                    "id": "ws-1",
                    "tenant_id": "acme",
                    "name": "Workspace 1",
                    "status": "active",
                    "created_at": "2026-05-22T17:00:00Z",
                    "updated_at": "2026-05-22T17:00:00Z",
                }
            ]
        )
        rows = await client.list_workspaces()
        assert len(rows) == 1
        assert isinstance(rows[0], WorkspaceSummary)
        assert rows[0].id == "ws-1"


@pytest.mark.asyncio
async def test_get_workspace_returns_spec() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        mock.get("/api/v1/workspaces/ws-1").respond(
            json={
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Workspace 1",
                "status": "active",
                "scope": [{"a": 1}],
                "sme_roster": None,
                "retention_days": 30,
                "jurisdiction": "ES",
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:00Z",
                "closed_at": None,
            }
        )
        spec = await client.get_workspace("ws-1")
        assert spec.scope == [{"a": 1}]
        assert spec.retention_days == 30
        assert spec.jurisdiction == "ES"


@pytest.mark.asyncio
async def test_update_workspace_patches_sparse_fields() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        route = mock.patch("/api/v1/workspaces/ws-1").respond(
            json={
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Renamed",
                "status": "active",
                "scope": None,
                "sme_roster": None,
                "retention_days": None,
                "jurisdiction": None,
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:00Z",
                "closed_at": None,
            }
        )
        spec = await client.update_workspace("ws-1", WorkspaceUpdate(name="Renamed"))
        assert spec.name == "Renamed"
        # Body only carries the field that was actually patched.
        sent = route.calls.last.request.content.decode("utf-8")
        assert "Renamed" in sent
        assert "retention_days" not in sent


@pytest.mark.asyncio
async def test_close_workspace_uses_close_action_path() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        route = mock.post("/api/v1/workspaces/ws-1:close").respond(
            json={
                "id": "ws-1",
                "tenant_id": "acme",
                "name": "Workspace 1",
                "status": "closed",
                "scope": None,
                "sme_roster": None,
                "retention_days": None,
                "jurisdiction": None,
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:00Z",
                "closed_at": "2026-05-22T17:00:00Z",
            }
        )
        spec = await client.close_workspace("ws-1")
        assert spec.status == "closed"
        assert spec.closed_at is not None
        assert route.called


# ----------------------------------------------------------------------
# Agent token CRUD
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_agent_token_returns_token_once() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        mock.post("/api/v1/agent-tokens").respond(
            201,
            json={
                "id": "tok-1",
                "name": "harvester",
                "prefix": "canon_abc123",
                "workspace_allowlist": ["ws-1"],
                "scopes": ["agent.sources:ingest"],
                "rate_limit_rpm": 60,
                "expires_at": None,
                "created_at": "2026-05-22T17:00:00Z",
                "created_by": "alice",
                "revoked_at": None,
                "last_used_at": None,
                "token": "canon_abc123_secrethash_xyz789",
            },
        )
        created = await client.mint_agent_token(
            AgentTokenMintRequest(
                name="harvester",
                workspace_allowlist=["ws-1"],
                scopes=["agent.sources:ingest"],
                rate_limit_rpm=60,
            )
        )
        assert isinstance(created, AgentTokenCreated)
        assert created.token == "canon_abc123_secrethash_xyz789"
        assert created.prefix == "canon_abc123"


@pytest.mark.asyncio
async def test_list_agent_tokens_returns_summaries() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        mock.get("/api/v1/agent-tokens").respond(
            json=[
                {
                    "id": "tok-1",
                    "name": "harvester",
                    "prefix": "canon_abc123",
                    "workspace_allowlist": ["ws-1"],
                    "scopes": ["*"],
                    "rate_limit_rpm": None,
                    "expires_at": None,
                    "created_at": "2026-05-22T17:00:00Z",
                    "created_by": "alice",
                    "revoked_at": None,
                    "last_used_at": None,
                }
            ]
        )
        rows = await client.list_agent_tokens()
        assert len(rows) == 1
        assert isinstance(rows[0], AgentTokenSummary)
        # Summary never exposes the raw secret.
        assert not hasattr(rows[0], "token")


@pytest.mark.asyncio
async def test_revoke_agent_token_returns_none() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", tenant_id="acme", workspace_id="ws-1"
    ) as client:
        route = mock.delete("/api/v1/agent-tokens/tok-1").respond(204)
        result = await client.revoke_agent_token("tok-1")
        assert result is None
        assert route.called


# ----------------------------------------------------------------------
# Agent surface (8 methods) -- mandatory Idempotency-Key on POSTs
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_ingest_source_sends_idempotency_key() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon",
        tenant_id="acme",
        workspace_id="ws-1",
        agent_token="canon_abc",
    ) as client:
        route = mock.post("/api/v1/agent/sources").respond(
            201,
            json={
                "id": "src-1",
                "kind": "txt",
                "status": "ingested",
                "content_sha256": "deadbeef",
                "content_bytes": 4,
                "n_chunks": 1,
                "metadata": {},
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:01Z",
            },
        )
        record = await client.agent.ingest_source(
            SubmitSourceJsonPayload(content_base64="aGk=", filename="hi.md"),
            idempotency_key="op-2026-05-22-001",
        )
        assert isinstance(record, SourceRecord)
        request = route.calls.last.request
        assert request.headers["Idempotency-Key"] == "op-2026-05-22-001"
        assert request.headers["X-Agent-Token"] == "canon_abc"


@pytest.mark.asyncio
async def test_agent_ingest_source_rejects_empty_idempotency_key() -> None:
    async with CanonClient(base_url="http://canon") as client:
        with pytest.raises(ValueError):
            await client.agent.ingest_source(
                SubmitSourceJsonPayload(content_base64="aGk=", filename="hi.md"),
                idempotency_key="",
            )
        with pytest.raises(ValueError):
            await client.agent.ingest_source(
                SubmitSourceJsonPayload(content_base64="aGk=", filename="hi.md"),
                idempotency_key="   ",
            )


@pytest.mark.asyncio
async def test_agent_get_source_does_not_require_idempotency_key() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", agent_token="canon_abc"
    ) as client:
        route = mock.get("/api/v1/agent/sources/src-1").respond(
            json={
                "id": "src-1",
                "kind": "txt",
                "status": "ingested",
                "content_sha256": "deadbeef",
                "content_bytes": 4,
                "n_chunks": 1,
                "metadata": {},
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:01Z",
            }
        )
        record = await client.agent.get_source("src-1")
        assert record.id == "src-1"
        # GETs do not send Idempotency-Key.
        assert "Idempotency-Key" not in route.calls.last.request.headers


@pytest.mark.asyncio
async def test_agent_query_sends_idempotency_key() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", agent_token="canon_abc"
    ) as client:
        route = mock.post("/api/v1/agent/query").respond(
            json={
                "answer": "Yes.",
                "citations": [],
                "model": "anthropic:claude-sonnet-4-6",
                "elapsed_ms": 420,
                "no_answer": False,
            }
        )
        response = await client.agent.query(
            AnswerRequest(question="Is the sky blue?"),
            idempotency_key="op-1",
        )
        assert isinstance(response, AnswerResponse)
        assert route.calls.last.request.headers["Idempotency-Key"] == "op-1"


@pytest.mark.asyncio
async def test_agent_query_rejects_empty_idempotency_key() -> None:
    async with CanonClient(base_url="http://canon") as client:
        with pytest.raises(ValueError):
            await client.agent.query(
                AnswerRequest(question="hi"),
                idempotency_key="",
            )


@pytest.mark.asyncio
async def test_agent_search_sends_idempotency_key() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", agent_token="canon_abc"
    ) as client:
        route = mock.post("/api/v1/agent/search").respond(
            json={"hits": [], "elapsed_ms": 32}
        )
        response = await client.agent.search(
            SearchRequest(query="topic", top_k=5),
            idempotency_key="op-1",
        )
        assert isinstance(response, SearchResponse)
        assert route.calls.last.request.headers["Idempotency-Key"] == "op-1"


@pytest.mark.asyncio
async def test_agent_get_knowledge_returns_item() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", agent_token="canon_abc"
    ) as client:
        mock.get("/api/v1/agent/knowledge/ki-1").respond(
            json={
                "id": "ki-1",
                "status": "active",
                "current_version": 1,
                "title": "X",
                "domain": "process",
                "jurisdiction": "GLOBAL",
                "tags": [],
                "created_at": "2026-05-22T17:00:00Z",
                "updated_at": "2026-05-22T17:00:00Z",
            }
        )
        item = await client.agent.get_knowledge("ki-1")
        assert isinstance(item, KnowledgeItem)


@pytest.mark.asyncio
async def test_agent_get_provenance_returns_provenance() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", agent_token="canon_abc"
    ) as client:
        mock.get("/api/v1/agent/knowledge/ki-1/provenance").respond(
            json={
                "knowledge_item_id": "ki-1",
                "version": 1,
                "citations": [],
                "sources": [],
                "history": [],
            }
        )
        prov = await client.agent.get_provenance("ki-1")
        assert isinstance(prov, Provenance)
        assert prov.knowledge_item_id == "ki-1"


@pytest.mark.asyncio
async def test_agent_propose_candidates_sends_idempotency_key() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon", agent_token="canon_abc"
    ) as client:
        route = mock.post("/api/v1/agent/candidates:propose").respond(
            201,
            json=[
                {
                    "id": "cand-1",
                    "status": "proposed",
                    "source_id": "src-1",
                    "title": "Candidate 1",
                    "body": "...",
                    "domain": "process",
                    "jurisdiction": "GLOBAL",
                    "tags": [],
                    "citations": [],
                    "created_at": "2026-05-22T17:00:00Z",
                }
            ],
        )
        candidates = await client.agent.propose_candidates(
            ProposeCandidateRequest(source_id="src-1"),
            idempotency_key="op-1",
        )
        assert len(candidates) == 1
        assert candidates[0].id == "cand-1"
        assert route.calls.last.request.headers["Idempotency-Key"] == "op-1"


# ----------------------------------------------------------------------
# Workspace event DTO round-trip
# ----------------------------------------------------------------------


def test_workspace_created_event_round_trips() -> None:
    payload = {
        "tenant_id": "acme",
        "workspace_id": "ws-1",
        "occurred_at": "2026-05-22T17:00:00Z",
        "name": "Workspace 1",
        "scope": [{"a": 1}],
        "sme_roster": [{"role": "sme"}],
        "retention_days": 30,
        "jurisdiction": "ES",
    }
    event = WorkspaceCreated.model_validate(payload)
    assert event.event_type == "workspace.created"
    assert event.tenant_id == "acme"
    assert event.workspace_id == "ws-1"
    assert event.name == "Workspace 1"
    dumped = event.model_dump(mode="json")
    # Round-trip back through validate keeps the same payload shape.
    again = WorkspaceCreated.model_validate(dumped)
    assert again == event


def test_workspace_updated_event_round_trips() -> None:
    payload = {
        "tenant_id": "acme",
        "workspace_id": "ws-1",
        "occurred_at": "2026-05-22T17:00:00Z",
        "name": "Renamed",
        "scope": None,
        "sme_roster": None,
        "retention_days": None,
        "jurisdiction": None,
    }
    event = WorkspaceUpdated.model_validate(payload)
    assert event.event_type == "workspace.updated"
    assert event.name == "Renamed"
    round_tripped = WorkspaceUpdated.model_validate(event.model_dump(mode="json"))
    assert round_tripped == event


def test_workspace_deleted_event_round_trips() -> None:
    payload = {
        "tenant_id": "acme",
        "workspace_id": "ws-1",
        "occurred_at": "2026-05-22T17:00:00Z",
    }
    event = WorkspaceDeleted.model_validate(payload)
    assert event.event_type == "workspace.deleted"
    assert event.tenant_id == "acme"
    assert event.workspace_id == "ws-1"
    round_tripped = WorkspaceDeleted.model_validate(event.model_dump(mode="json"))
    assert round_tripped == event
