# Copyright 2026 Firefly Software Solutions Inc
"""Async client smoke tests against a respx-mocked transport."""

from __future__ import annotations

import pytest
import respx

from flycanon_sdk import (
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
    KnowledgeDiff,
    KnowledgeGraph,
    LatencyReport,
    RelationsList,
    SourceMetadata,
    StaleReport,
    SubjectCostReport,
    SubmitSourceJsonPayload,
    SuggestionsResponse,
    TopConsumersReport,
    VersionInfo,
)


@pytest.mark.asyncio
async def test_version_returns_version_info() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
        mock.get("/api/v1/knowledge/ki-1/diff").respond(
            json={
                "knowledge_item_id": "ki-1",
                "from_version": 1,
                "to_version": 2,
                "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
                "field_changes": [
                    {"field": "title", "from": "Old", "to": "New"}
                ],
                "added_citations": [],
                "removed_citations": [],
            }
        )
        diff = await client.get_diff("ki-1", from_version=1, to_version=2)
        assert isinstance(diff, KnowledgeDiff)
        assert diff.field_changes[0].field == "title"


@pytest.mark.asyncio
async def test_list_relations_returns_outgoing_incoming() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
        turn = await client.add_turn(
            "c-1", CreateConversationTurnRequest(query="what?")
        )
        assert turn.answer == "yes"


@pytest.mark.asyncio
async def test_suggest_questions_returns_list() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
        mock.post("/api/v1/conversations/c-1/suggest").respond(
            json={"questions": ["What about X?", "Y?"]}
        )
        suggestions = await client.suggest_questions("c-1")
        assert isinstance(suggestions, SuggestionsResponse)
        assert len(suggestions.questions) == 2


@pytest.mark.asyncio
async def test_billing_report_returns_rows() -> None:
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
    async with respx.mock(base_url="http://canon") as mock, CanonClient(
        base_url="http://canon"
    ) as client:
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
