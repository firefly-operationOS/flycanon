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

"""Shared test fixtures.

The unit tests run against an in-memory SQLite engine and stubbed
agentic primitives (mock embedder, mock vector store, mock corpus).
No external services required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flycanon.models.entities import Base
from flycanon.models.repositories.audit_repository import AuditRepository
from flycanon.models.repositories.candidate_repository import CandidateRepository
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.conversation_repository import ConversationRepository
from flycanon.models.repositories.cost_repository import CostRepository
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
        "cost": _wrap(session_factory, engine, CostRepository),
    }


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def scope() -> dict[str, str]:
    """Canonical (tenant_id, workspace_id) for unit-test fixtures.

    Every ``canon_*`` row carries a non-null ``tenant_id`` +
    ``workspace_id``, and service-layer callers MUST pass scope
    kwargs explicitly. Unit tests don't care which scope they sit
    under, so this fixture supplies a stable ``("default", "default")``
    pair every test can spread into a service call.
    """
    return {"tenant_id": "default", "workspace_id": "default"}
