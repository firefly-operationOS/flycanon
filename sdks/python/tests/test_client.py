# Copyright 2026 Firefly Software Solutions Inc
"""Async client smoke tests against a respx-mocked transport."""

from __future__ import annotations

import httpx
import pytest
import respx

from flycanon_sdk import (
    AnswerResponse,
    CanonAPIError,
    CanonClient,
    Hit,
    KnowledgeItem,
    ProposeCandidateRequest,
    SourceMetadata,
    SubmitSourceJsonPayload,
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
                "version": "26.5.1",
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
