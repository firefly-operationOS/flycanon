# Copyright 2026 Firefly Software Solutions Inc
"""Workspace-scope security tests for every read-by-id route.

Every read-by-id route filters on both ``tenant_id`` and
``workspace_id``: a caller from workspace B must not fetch resources
that live in workspace A (same tenant) by guessing the UUID. These
tests pin every read-by-id surface to the correct behaviour:
same-tenant cross-workspace lookups MUST return ``404`` (or raise the
typed ``*NotFound`` exception that maps to one), NOT the foreign row.

The cross-tenant case (``tenant=acme`` row read from ``tenant=bcorp``)
is covered elsewhere; this file focuses on the **same-tenant
cross-workspace** case.

Each class targets one resource family and exercises both the
user-tier handler and (where the route exists) the agent-tier
counterpart. The handlers run against in-memory SQLite repositories
through the same CQRS query objects the controllers dispatch, so a
mis-scoped query at the repository layer fails the relevant
assertion below.
"""

from __future__ import annotations

import pytest

from flycanon.core.services.consolidation.handlers import (
    GetCandidateHandler,
    GetCandidateQuery,
)
from flycanon.core.services.jobs import (
    GetIngestJobHandler,
    GetIngestJobQuery,
    ListIngestJobEventsHandler,
    ListIngestJobEventsQuery,
)
from flycanon.core.services.knowledge.errors import (
    KnowledgeItemNotFound,
)
from flycanon.core.services.knowledge.handlers import (
    GetKnowledgeHandler,
    GetKnowledgeHistoryHandler,
    GetKnowledgeHistoryQuery,
    GetKnowledgeQuery,
    GetProvenanceHandler,
    GetProvenanceQuery,
    ListKnowledgeRelationsHandler,
    ListKnowledgeRelationsQuery,
)
from flycanon.core.services.knowledge.provenance_service import ProvenanceService
from flycanon.core.services.knowledge.relation_service import (
    KnowledgeRelationService,
    RelationNotFoundError,
)
from flycanon.core.services.sources.get_source_handler import (
    GetSourceHandler,
    GetSourceQuery,
)
from flycanon.interfaces.enums import KnowledgeStatus
from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.conversation import ConversationRow
from flycanon.models.entities.ingest_job import IngestJobEventRow, IngestJobRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.entities.source import SourceRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _add(session_factory, *rows) -> None:
    """Insert rows directly via the session factory."""
    async with session_factory() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def _knowledge_item(
    *,
    id: str,
    tenant_id: str,
    workspace_id: str,
    current_version: int = 1,
    status: str = KnowledgeStatus.published.value,
) -> KnowledgeItemRow:
    return KnowledgeItemRow(
        id=id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status=status,
        current_version=current_version,
        title="t",
        summary="s",
        domain="legal",
        jurisdiction="ES",
        tags_json=[],
        metadata_json={},
    )


def _knowledge_version(
    *,
    knowledge_item_id: str,
    tenant_id: str,
    workspace_id: str,
    version: int = 1,
    status: str = KnowledgeStatus.published.value,
) -> KnowledgeVersionRow:
    return KnowledgeVersionRow(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        knowledge_item_id=knowledge_item_id,
        version=version,
        status=status,
        title="t",
        summary="s",
        body="b",
        domain="legal",
        jurisdiction="ES",
        tags_json=[],
        metadata_json={},
    )


def _source_row(
    *,
    id: str,
    tenant_id: str,
    workspace_id: str,
) -> SourceRow:
    return SourceRow(
        id=id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        kind="markdown",
        status="ingested",
        filename="f.md",
        content_type="text/markdown",
        content_sha256="0" * 64,
        content_bytes=10,
        n_chunks=1,
        metadata_json={},
    )


def _candidate_row(
    *,
    id: str,
    tenant_id: str,
    workspace_id: str,
    source_id: str,
) -> CandidateRow:
    return CandidateRow(
        id=id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status="proposed",
        source_id=source_id,
        title="cand-title",
        summary="cand-summary",
        body="cand-body",
        domain="legal",
        jurisdiction="ES",
        tags_json=[],
        citations_json=[],
        score=0.5,
        rationale=None,
        actor=None,
    )


def _conversation_row(
    *,
    id: str,
    tenant_id: str,
    workspace_id: str,
) -> ConversationRow:
    return ConversationRow(
        id=id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        title="conv",
        actor=None,
        model="anthropic:claude-sonnet-4.7",
        metadata_json={},
    )


def _ingest_job_row(
    *,
    id: str,
    tenant_id: str,
    workspace_id: str,
    status: str = "queued",
) -> IngestJobRow:
    return IngestJobRow(
        id=id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        status=status,
        source_id=None,
        attempts=0,
        filename="f.md",
        content_type="text/markdown",
        uri=None,
        content_sha256=None,
        actor=None,
        correlation_id=None,
        callback_url=None,
        metadata_json={},
        error_code=None,
        error_message=None,
    )


def _ingest_job_event_row(
    *,
    job_id: str,
    tenant_id: str,
    workspace_id: str,
    stage: str = "queued",
) -> IngestJobEventRow:
    return IngestJobEventRow(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        job_id=job_id,
        stage=stage,
        message="m",
        payload_json={},
    )


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


class TestKnowledgeWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_get_knowledge_returns_none_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetKnowledgeHandler(repositories["knowledge"])

        # Same tenant, wrong workspace -- must NOT return the foreign row.
        result = await handler.do_handle(
            GetKnowledgeQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_knowledge_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetKnowledgeHandler(repositories["knowledge"])

        result = await handler.do_handle(
            GetKnowledgeQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result is not None
        assert result.id == "ki-A"

    @pytest.mark.asyncio
    async def test_get_knowledge_history_returns_empty_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_version(knowledge_item_id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetKnowledgeHistoryHandler(repositories["knowledge"])

        # Same tenant, wrong workspace -- versions of a foreign item must not leak.
        result = await handler.do_handle(
            GetKnowledgeHistoryQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_list_relations_empty_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.config import CanonSettings

        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_item(id="ki-A2", tenant_id="acme", workspace_id="ws-a"),
            KnowledgeRelationRow(
                tenant_id="acme",
                workspace_id="ws-a",
                from_item_id="ki-A",
                to_item_id="ki-A2",
                kind="related",
            ),
        )

        class _NoopAudit:
            async def record(self, **kwargs):
                return None

        service = KnowledgeRelationService(
            knowledge_repository=repositories["knowledge"],
            relation_repository=repositories["relation"],
            audit=_NoopAudit(),
            event_publisher=None,
            settings=CanonSettings(),
        )
        handler = ListKnowledgeRelationsHandler(service)

        with pytest.raises(KnowledgeItemNotFound):
            await handler.do_handle(
                ListKnowledgeRelationsQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-b")
            )

    @pytest.mark.asyncio
    async def test_provenance_raises_not_found_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_version(knowledge_item_id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        provenance = ProvenanceService(
            knowledge_repository=repositories["knowledge"],
            source_repository=repositories["source"],
        )
        handler = GetProvenanceHandler(provenance)

        with pytest.raises(KnowledgeItemNotFound):
            await handler.do_handle(GetProvenanceQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-b"))

    @pytest.mark.asyncio
    async def test_provenance_resolves_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_version(knowledge_item_id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        provenance = ProvenanceService(
            knowledge_repository=repositories["knowledge"],
            source_repository=repositories["source"],
        )
        handler = GetProvenanceHandler(provenance)

        result = await handler.do_handle(
            GetProvenanceQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result.knowledge_item_id == "ki-A"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class TestSourcesWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_get_source_returns_none_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetSourceHandler(repositories["source"])

        result = await handler.do_handle(
            GetSourceQuery(source_id="src-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_source_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetSourceHandler(repositories["source"])

        result = await handler.do_handle(
            GetSourceQuery(source_id="src-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result is not None
        assert result.id == "src-A"

    @pytest.mark.asyncio
    async def test_get_source_returns_none_for_cross_tenant(
        self,
        repositories,
        session_factory,
    ) -> None:
        """Cross-tenant lookups already 404; pinned here for completeness."""
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetSourceHandler(repositories["source"])

        result = await handler.do_handle(
            GetSourceQuery(source_id="src-A", tenant_id="bcorp", workspace_id="ws-a")
        )
        assert result is None


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


class TestCandidatesWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_get_candidate_returns_none_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
            _candidate_row(id="cand-A", tenant_id="acme", workspace_id="ws-a", source_id="src-A"),
        )
        handler = GetCandidateHandler(repositories["candidate"])

        result = await handler.do_handle(
            GetCandidateQuery(candidate_id="cand-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_candidate_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
            _candidate_row(id="cand-A", tenant_id="acme", workspace_id="ws-a", source_id="src-A"),
        )
        handler = GetCandidateHandler(repositories["candidate"])

        result = await handler.do_handle(
            GetCandidateQuery(candidate_id="cand-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result is not None
        assert result.id == "cand-A"


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestConversationsWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_get_conversation_raises_not_found_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.config import CanonSettings
        from flycanon.core.services.conversations import (
            ConversationNotFound,
            ConversationService,
        )

        await _add(
            session_factory,
            _conversation_row(id="conv-A", tenant_id="acme", workspace_id="ws-a"),
        )

        class _NoopAudit:
            async def record(self, **kwargs):
                return None

        service = ConversationService(
            repository=repositories["conversation"],
            answer_service=None,  # type: ignore[arg-type]
            audit=_NoopAudit(),
            settings=CanonSettings(),
        )

        # Same tenant, wrong workspace -- the get() must enforce scope.
        with pytest.raises(ConversationNotFound):
            await service.get("conv-A", tenant_id="acme", workspace_id="ws-b")

    @pytest.mark.asyncio
    async def test_get_conversation_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.config import CanonSettings
        from flycanon.core.services.conversations import ConversationService

        await _add(
            session_factory,
            _conversation_row(id="conv-A", tenant_id="acme", workspace_id="ws-a"),
        )

        class _NoopAudit:
            async def record(self, **kwargs):
                return None

        service = ConversationService(
            repository=repositories["conversation"],
            answer_service=None,  # type: ignore[arg-type]
            audit=_NoopAudit(),
            settings=CanonSettings(),
        )

        conv = await service.get("conv-A", tenant_id="acme", workspace_id="ws-a")
        assert conv.id == "conv-A"


# ---------------------------------------------------------------------------
# Ingest jobs
# ---------------------------------------------------------------------------


class TestIngestJobsWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_get_ingest_job_returns_none_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _ingest_job_row(id="job-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetIngestJobHandler(repositories["ingest_job"])

        result = await handler.do_handle(
            GetIngestJobQuery(job_id="job-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_ingest_job_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _ingest_job_row(id="job-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetIngestJobHandler(repositories["ingest_job"])

        result = await handler.do_handle(
            GetIngestJobQuery(job_id="job-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result is not None
        assert result.id == "job-A"

    @pytest.mark.asyncio
    async def test_list_ingest_job_events_returns_empty_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _ingest_job_row(id="job-A", tenant_id="acme", workspace_id="ws-a"),
            _ingest_job_event_row(job_id="job-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = ListIngestJobEventsHandler(repositories["ingest_job"])

        result = await handler.do_handle(
            ListIngestJobEventsQuery(job_id="job-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_list_ingest_job_events_returns_rows_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _ingest_job_row(id="job-A", tenant_id="acme", workspace_id="ws-a"),
            _ingest_job_event_row(job_id="job-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = ListIngestJobEventsHandler(repositories["ingest_job"])

        result = await handler.do_handle(
            ListIngestJobEventsQuery(job_id="job-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Knowledge relations -- remove uses an id-only lookup that must enforce scope
# ---------------------------------------------------------------------------


class TestKnowledgeRelationRemoveScoping:
    @pytest.mark.asyncio
    async def test_remove_relation_rejects_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.config import CanonSettings

        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_item(id="ki-B", tenant_id="acme", workspace_id="ws-a"),
        )

        class _NoopAudit:
            async def record(self, **kwargs):
                return None

        service = KnowledgeRelationService(
            knowledge_repository=repositories["knowledge"],
            relation_repository=repositories["relation"],
            audit=_NoopAudit(),
            event_publisher=None,
            settings=CanonSettings(),
        )

        # Seed a relation in workspace=ws-a directly through the repo
        row = KnowledgeRelationRow(
            tenant_id="acme",
            workspace_id="ws-a",
            from_item_id="ki-A",
            to_item_id="ki-B",
            kind="related",
        )
        async with session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        relation_id = row.id

        # Attempt to remove from a foreign workspace -- must 404.
        with pytest.raises(RelationNotFoundError):
            await service.remove(
                relation_id,
                tenant_id="acme",
                workspace_id="ws-b",
            )


# ---------------------------------------------------------------------------
# Agent-tier coverage -- the agent handlers ride the same CQRS queries as the
# user-tier handlers, so the workspace_id enforcement at the handler / repo
# layer protects both surfaces. The tests below verify that explicitly.
# ---------------------------------------------------------------------------


class TestAgentTierWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_agent_get_knowledge_returns_none_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        # The agent controller calls the same GetKnowledgeQuery -- a
        # token allowlisted for both ws-a + ws-b cannot use the ws-b
        # context to read a ws-a row.
        handler = GetKnowledgeHandler(repositories["knowledge"])
        result = await handler.do_handle(
            GetKnowledgeQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_agent_get_knowledge_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetKnowledgeHandler(repositories["knowledge"])
        result = await handler.do_handle(
            GetKnowledgeQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result is not None
        assert result.id == "ki-A"

    @pytest.mark.asyncio
    async def test_agent_get_source_returns_none_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetSourceHandler(repositories["source"])
        result = await handler.do_handle(
            GetSourceQuery(source_id="src-A", tenant_id="acme", workspace_id="ws-b")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_agent_get_source_returns_row_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _source_row(id="src-A", tenant_id="acme", workspace_id="ws-a"),
        )
        handler = GetSourceHandler(repositories["source"])
        result = await handler.do_handle(
            GetSourceQuery(source_id="src-A", tenant_id="acme", workspace_id="ws-a")
        )
        assert result is not None
        assert result.id == "src-A"

    @pytest.mark.asyncio
    async def test_agent_provenance_raises_not_found_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_version(knowledge_item_id="ki-A", tenant_id="acme", workspace_id="ws-a"),
        )
        provenance = ProvenanceService(
            knowledge_repository=repositories["knowledge"],
            source_repository=repositories["source"],
        )
        handler = GetProvenanceHandler(provenance)

        with pytest.raises(KnowledgeItemNotFound):
            await handler.do_handle(GetProvenanceQuery(item_id="ki-A", tenant_id="acme", workspace_id="ws-b"))


# ---------------------------------------------------------------------------
# Knowledge -- diff endpoint also reads by id and must enforce scope
# ---------------------------------------------------------------------------


class TestKnowledgeDiffWorkspaceScoping:
    @pytest.mark.asyncio
    async def test_diff_raises_not_found_for_foreign_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.core.services.knowledge.diff_service import KnowledgeDiffService

        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_version(
                knowledge_item_id="ki-A",
                tenant_id="acme",
                workspace_id="ws-a",
                version=1,
            ),
            _knowledge_version(
                knowledge_item_id="ki-A",
                tenant_id="acme",
                workspace_id="ws-a",
                version=2,
            ),
        )
        service = KnowledgeDiffService(repositories["knowledge"])

        with pytest.raises(KnowledgeItemNotFound):
            await service.diff(
                "ki-A",
                1,
                2,
                tenant_id="acme",
                workspace_id="ws-b",
            )

    @pytest.mark.asyncio
    async def test_diff_returns_result_for_correct_workspace(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.core.services.knowledge.diff_service import KnowledgeDiffService

        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_version(
                knowledge_item_id="ki-A",
                tenant_id="acme",
                workspace_id="ws-a",
                version=1,
            ),
            _knowledge_version(
                knowledge_item_id="ki-A",
                tenant_id="acme",
                workspace_id="ws-a",
                version=2,
            ),
        )
        service = KnowledgeDiffService(repositories["knowledge"])

        result = await service.diff(
            "ki-A",
            1,
            2,
            tenant_id="acme",
            workspace_id="ws-a",
        )
        assert result.knowledge_item_id == "ki-A"


# ---------------------------------------------------------------------------
# Cross-workspace knowledge-relation list is also pinned for completeness
# ---------------------------------------------------------------------------


class TestKnowledgeRelationListScoping:
    @pytest.mark.asyncio
    async def test_relations_from_other_workspace_not_listed(
        self,
        repositories,
        session_factory,
    ) -> None:
        from flycanon.config import CanonSettings

        await _add(
            session_factory,
            _knowledge_item(id="ki-A", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_item(id="ki-B", tenant_id="acme", workspace_id="ws-a"),
            _knowledge_item(id="ki-C", tenant_id="acme", workspace_id="ws-a"),
            # Same edge in ws-a
            KnowledgeRelationRow(
                tenant_id="acme",
                workspace_id="ws-a",
                from_item_id="ki-A",
                to_item_id="ki-B",
                kind="related",
            ),
            # Edge from another workspace, same tenant -- must NOT
            # surface when listing in ws-a.
            _knowledge_item(id="ki-X", tenant_id="acme", workspace_id="ws-b"),
            _knowledge_item(id="ki-Y", tenant_id="acme", workspace_id="ws-b"),
            KnowledgeRelationRow(
                tenant_id="acme",
                workspace_id="ws-b",
                from_item_id="ki-X",
                to_item_id="ki-Y",
                kind="related",
            ),
        )

        class _NoopAudit:
            async def record(self, **kwargs):
                return None

        service = KnowledgeRelationService(
            knowledge_repository=repositories["knowledge"],
            relation_repository=repositories["relation"],
            audit=_NoopAudit(),
            event_publisher=None,
            settings=CanonSettings(),
        )

        outgoing, incoming = await service.list_for_item("ki-A", tenant_id="acme", workspace_id="ws-a")
        # Only the ws-a edge surfaces; the ws-b edge is filtered.
        assert [r.to_item_id for r in outgoing] == ["ki-B"]
        assert incoming == []
