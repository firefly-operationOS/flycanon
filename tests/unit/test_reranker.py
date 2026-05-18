# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the reranker protocol + builder + adapters."""

from __future__ import annotations

import httpx
import pytest
import respx

from flycanon.core.services.retrieval.reranker import (
    CohereReranker,
    NoOpReranker,
    VoyageReranker,
    build_reranker,
)


class _Hit:
    def __init__(self, content: str) -> None:
        self.content = content


class TestBuilder:
    def test_empty_model_returns_noop(self):
        assert isinstance(build_reranker(""), NoOpReranker)

    def test_cohere_model_returns_cohere_adapter(self):
        assert isinstance(
            build_reranker("cohere:rerank-multilingual-v3.0"), CohereReranker
        )

    def test_voyage_model_returns_voyage_adapter(self):
        assert isinstance(build_reranker("voyageai:rerank-2"), VoyageReranker)

    def test_unknown_provider_falls_back_to_noop(self):
        assert isinstance(build_reranker("acme:rerank-xl"), NoOpReranker)


class TestNoOp:
    @pytest.mark.asyncio
    async def test_returns_input_unchanged(self):
        hits = [_Hit("a"), _Hit("b"), _Hit("c")]
        out = await NoOpReranker().rerank(query="q", hits=hits, top_n=10)
        assert out == hits


class TestCohereAdapter:
    @pytest.mark.asyncio
    async def test_missing_api_key_short_circuits(self, monkeypatch):
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        hits = [_Hit("a"), _Hit("b")]
        out = await CohereReranker("cohere:rerank-v3").rerank(
            query="q", hits=hits, top_n=2
        )
        assert out == hits

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_reorders_per_provider_index(self, respx_mock, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "sk-test")
        respx_mock.post("https://api.cohere.com/v2/rerank").mock(
            return_value=httpx.Response(
                200,
                json={
                    # Reverse the input order.
                    "results": [
                        {"index": 2, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.7},
                        {"index": 1, "relevance_score": 0.5},
                    ]
                },
            )
        )
        hits = [_Hit("a"), _Hit("b"), _Hit("c")]
        out = await CohereReranker("cohere:rerank-v3").rerank(
            query="q", hits=hits, top_n=3
        )
        assert [h.content for h in out] == ["c", "a", "b"]

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_provider_error_falls_back_to_input(
        self, respx_mock, monkeypatch
    ):
        monkeypatch.setenv("COHERE_API_KEY", "sk-test")
        respx_mock.post("https://api.cohere.com/v2/rerank").mock(
            return_value=httpx.Response(500, text="boom")
        )
        hits = [_Hit("a"), _Hit("b")]
        out = await CohereReranker("cohere:rerank-v3").rerank(
            query="q", hits=hits, top_n=2
        )
        assert out == hits


class TestVoyageAdapter:
    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_voyage_reorders(self, respx_mock, monkeypatch):
        monkeypatch.setenv("VOYAGEAI_API_KEY", "sk-voyage")
        respx_mock.post("https://api.voyageai.com/v1/rerank").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "relevance_score": 0.95},
                        {"index": 0, "relevance_score": 0.50},
                    ]
                },
            )
        )
        hits = [_Hit("a"), _Hit("b")]
        out = await VoyageReranker("voyageai:rerank-2").rerank(
            query="q", hits=hits, top_n=2
        )
        assert [h.content for h in out] == ["b", "a"]
