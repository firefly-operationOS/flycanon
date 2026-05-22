# Copyright 2026 Firefly Software Solutions Inc
"""Scope threading coverage for :class:`IndexService`.

The write surface uses a SOFT default: ``tenant_id`` /
``workspace_id`` are optional kwargs that fall back to ``'default'``
so existing ingestion callers keep working until Plan 4 wires real
scope through the intake handlers. The column-level server defaults
(Plan 2) catch forgotten writes at the database layer.

Once a caller does pass real values, the kwargs MUST land on the
underlying ``vector_store.upsert`` (only the flycanon
``PgVectorVectorStore`` accepts scope kwargs today; agentic backends
ignore the extras and rely on the canon_chunks scope filter on
read-hydration as the safety net).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest

from flycanon.core.services.retrieval.corpus_factory import CorpusContext
from flycanon.core.services.retrieval.index_service import IndexService


class _FakeCorpus:
    """Stand-in -- the only thing we care about is the upsert pass-through."""

    def __init__(self) -> None:
        self.delete_calls: list[str] = []
        self.upsert_calls: list[Sequence[Any]] = []

    async def initialise(self) -> None:
        return None

    async def delete_by_doc_id(self, doc_id: str) -> int:
        self.delete_calls.append(doc_id)
        return 0

    async def upsert_chunks(self, chunks: Sequence[Any]) -> None:
        self.upsert_calls.append(chunks)


class _FakeScopeAwareVectorStore:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, Any]] = []

    async def upsert(
        self,
        documents: list[Any],
        namespace: str = "default",
        *,
        tenant_id: str = "default",
        workspace_id: str = "default",
    ) -> None:
        self.upsert_calls.append(
            {
                "documents": documents,
                "namespace": namespace,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        )


class _FakeLegacyVectorStore:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, Any]] = []

    async def upsert(self, documents: list[Any], namespace: str = "default") -> None:
        # The agentic protocol signature -- no scope kwargs.
        self.upsert_calls.append({"documents": documents, "namespace": namespace})


def _source(id_: str = "src-1"):
    """Stub :class:`SourceRow` -- we only read a handful of attrs."""
    s = MagicMock()
    s.id = id_
    s.kind = "doc"
    s.filename = "f.md"
    s.uri = None
    return s


def _chunk(id_: str, *, index: int = 0):
    """Stub :class:`KnowledgeChunkRow` -- mutable attrs only."""
    c = MagicMock()
    c.id = id_
    c.content = "hello"
    c.section_path = None
    c.page = None
    c.index_in_source = index
    c.embedding_model = None
    return c


def _make_index_service(*, corpus, vector_store) -> IndexService:
    return IndexService(
        context=CorpusContext(corpus=corpus, vector_store=vector_store, backend="test"),
    )


class TestReplaceForSourceSignature:
    def test_accepts_optional_scope(self):
        # Soft default: the kwargs MUST be optional so existing
        # callers can call replace_for_source without them.
        import inspect

        sig = inspect.signature(IndexService.replace_for_source)
        params = sig.parameters
        assert "tenant_id" in params
        assert "workspace_id" in params
        assert params["tenant_id"].default == "default"
        assert params["workspace_id"].default == "default"


class TestReplaceForSourceWritePathSoftDefault:
    @pytest.mark.asyncio
    async def test_defaults_to_default_scope_when_not_passed(self):
        # The existing ingestion handlers haven't been migrated to
        # Plan 4 yet -- they call replace_for_source without scope.
        # The service MUST keep working, falling back to 'default'
        # so the column-level server default catches the write.
        corpus = _FakeCorpus()
        vector_store = _FakeScopeAwareVectorStore()
        svc = _make_index_service(corpus=corpus, vector_store=vector_store)

        chunk = _chunk("ch-1")
        await svc.replace_for_source(
            source=_source(),
            chunks=[chunk],
            embeddings=[[0.1, 0.2, 0.3]],
            embedding_model="fake",
        )
        # Vector-store upsert saw the default scope.
        assert len(vector_store.upsert_calls) == 1
        assert vector_store.upsert_calls[0]["tenant_id"] == "default"
        assert vector_store.upsert_calls[0]["workspace_id"] == "default"

    @pytest.mark.asyncio
    async def test_passes_explicit_scope_to_vector_store(self):
        corpus = _FakeCorpus()
        vector_store = _FakeScopeAwareVectorStore()
        svc = _make_index_service(corpus=corpus, vector_store=vector_store)

        chunk = _chunk("ch-1")
        await svc.replace_for_source(
            source=_source(),
            chunks=[chunk],
            embeddings=[[0.1, 0.2, 0.3]],
            embedding_model="fake",
            tenant_id="acme",
            workspace_id="ws-a",
        )
        assert vector_store.upsert_calls[0]["tenant_id"] == "acme"
        assert vector_store.upsert_calls[0]["workspace_id"] == "ws-a"


class TestReplaceForSourceLegacyVectorStore:
    @pytest.mark.asyncio
    async def test_does_not_pass_scope_to_unsupported_store(self):
        # Agentic backends (InMemory, SqliteVec, Chroma) don't accept
        # scope kwargs -- passing tenant_id would raise TypeError.
        # IndexService must probe and call them without the extras.
        corpus = _FakeCorpus()
        vector_store = _FakeLegacyVectorStore()
        svc = _make_index_service(corpus=corpus, vector_store=vector_store)

        chunk = _chunk("ch-1")
        await svc.replace_for_source(
            source=_source(),
            chunks=[chunk],
            embeddings=[[0.1, 0.2, 0.3]],
            embedding_model="fake",
            tenant_id="acme",
            workspace_id="ws-a",
        )
        # Legacy store saw only the documents kwargs, no scope.
        assert len(vector_store.upsert_calls) == 1
        assert "tenant_id" not in vector_store.upsert_calls[0]
        assert "workspace_id" not in vector_store.upsert_calls[0]
