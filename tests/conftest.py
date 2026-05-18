# Copyright 2026 Firefly Software Solutions Inc
"""Shared test fixtures.

The unit tests run against an in-memory SQLite engine and stubbed
agentic primitives (mock embedder, mock vector store, mock corpus).
No external services required.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flycanon.models.entities import Base
from flycanon.models.repositories.audit_repository import AuditRepository
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.conversation_repository import ConversationRepository
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.relation_repository import RelationRepository
from flycanon.models.repositories.source_repository import SourceRepository
from flycanon.models.repositories.taxonomy_repository import TaxonomyRepository


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _wrap(session_factory, engine, cls):
    return cls(session_factory, engine=engine)


@pytest_asyncio.fixture
async def repositories(engine, session_factory):
    return {
        "source": _wrap(session_factory, engine, SourceRepository),
        "chunk": _wrap(session_factory, engine, ChunkRepository),
        "knowledge": _wrap(session_factory, engine, KnowledgeRepository),
        "candidate": _wrap(session_factory, engine, CandidateRepository),
        "audit": _wrap(session_factory, engine, AuditRepository),
        "taxonomy": _wrap(session_factory, engine, TaxonomyRepository),
        "relation": _wrap(session_factory, engine, RelationRepository),
        "ingest_job": _wrap(session_factory, engine, IngestJobRepository),
        "conversation": _wrap(session_factory, engine, ConversationRepository),
    }


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
