# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end coverage for the ``/api/v1/agent/*`` controllers.

Exercises the eight agent-tier endpoints added in Plan 5 Task 5:

* ``POST /api/v1/agent/sources``                       (idempotent)
* ``GET  /api/v1/agent/sources/{id}``
* ``POST /api/v1/agent/query``                         (idempotent)
* ``POST /api/v1/agent/query/stream``                  (idempotent)
* ``POST /api/v1/agent/search``                        (idempotent)
* ``GET  /api/v1/agent/knowledge/{id}``
* ``GET  /api/v1/agent/knowledge/{id}/provenance``
* ``POST /api/v1/agent/candidates:propose``            (idempotent)

Each endpoint is covered by a happy path, the four auth-failure
modes (missing token, invalid scope, expired token, workspace
not in allowlist), and the missing-idempotency-key failure for
the five POSTs. The tests drive the controllers directly with a
stubbed Starlette :class:`Request` and a mocked CQRS bus -- the
auth gate (``X-Agent-Token`` -> ``AgentTokenService.verify`` ->
:class:`TenantContext`) is exercised end-to-end against a real
in-memory repository, mirroring ``test_agent_tokens_crud.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from flycanon.core.services.auth.agent_token_service import (
    AgentScopeDenied,
    AgentTokenExpired,
    AgentTokenService,
    AgentWorkspaceNotInAllowlist,
    InvalidAgentToken,
    MintRequest,
)
from flycanon.web.agent_deps import MissingAgentToken
from flycanon.web.controllers.agent.candidates_controller import (
    AgentCandidatesController,
)
from flycanon.web.controllers.agent.knowledge_controller import (
    AgentKnowledgeController,
)
from flycanon.web.controllers.agent.query_controller import AgentQueryController
from flycanon.web.controllers.agent.sources_controller import AgentSourcesController
from flycanon.web.conventions import InMemoryIdempotencyStore, MissingIdempotencyKey

# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------


class _InMemoryAgentTokenRepository:
    """Mirror of the production repository's contract."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def insert(self, row: dict) -> None:
        self._rows[row["id"]] = row

    async def get_by_prefix(self, prefix: str, *, tenant_id: str) -> dict | None:
        for row in self._rows.values():
            if row["prefix"] == prefix and row["tenant_id"] == tenant_id and row["revoked_at"] is None:
                return row
        return None

    async def list_for_tenant(self, tenant_id: str) -> list[dict]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def revoke(self, token_id: str, *, tenant_id: str, at: datetime) -> bool:
        row = self._rows.get(token_id)
        if not row or row["tenant_id"] != tenant_id or row["revoked_at"] is not None:
            return False
        row["revoked_at"] = at
        return True

    async def mark_used(self, token_id: str, *, tenant_id: str, at: datetime) -> None:
        row = self._rows.get(token_id)
        if row is not None and row["tenant_id"] == tenant_id:
            row["last_used_at"] = at


class _StubRequest:
    """Starlette-compatible Request stub with a headers dict.

    ``tenant_context_from_request`` only reads ``request.headers.get(...)``;
    a thin class with a dict-like ``headers`` is enough to feed the
    conventions helper without a full Starlette Request.
    """

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


_TENANT = "acme"
_WORKSPACE = "ws-agent-test"


@pytest.fixture
def repo() -> _InMemoryAgentTokenRepository:
    return _InMemoryAgentTokenRepository()


@pytest.fixture
def agent_token_service(repo: _InMemoryAgentTokenRepository) -> AgentTokenService:
    return AgentTokenService(repo)


async def _mint(
    service: AgentTokenService,
    *,
    scopes: list[str],
    workspace_allowlist: list[str] | None = None,
    expires_at: datetime | None = None,
    name: str = "test-token",
) -> str:
    """Mint a fresh agent token and return the raw secret."""
    minted = await service.mint(
        MintRequest(
            tenant_id=_TENANT,
            name=name,
            workspace_allowlist=workspace_allowlist,
            scopes=scopes,
            rate_limit_rpm=None,
            expires_at=expires_at,
        ),
        actor="anonymous",
    )
    return minted.token


def _request(
    token: str | None = None,
    *,
    idempotency_key: str | None = None,
    tenant_id: str = _TENANT,
    workspace_id: str = _WORKSPACE,
) -> _StubRequest:
    """Build a stub request carrying the standard agent headers."""
    headers: dict[str, str] = {
        "X-Tenant-Id": tenant_id,
        "X-Workspace-Id": workspace_id,
    }
    if token is not None:
        headers["X-Agent-Token"] = token
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return _StubRequest(headers)


def _command_bus(send_returns: Any = None) -> AsyncMock:
    """Mocked :class:`DefaultCommandBus` whose ``send`` returns ``send_returns``."""
    bus = AsyncMock()
    bus.send = AsyncMock(return_value=send_returns)
    return bus


def _query_bus(query_returns: Any = None) -> AsyncMock:
    """Mocked :class:`DefaultQueryBus` whose ``query`` returns ``query_returns``."""
    bus = AsyncMock()
    bus.query = AsyncMock(return_value=query_returns)
    return bus


# ---------------------------------------------------------------------
# Helper coverage
# ---------------------------------------------------------------------


class TestHelpers:
    """Coverage for the shared agent helpers."""

    @pytest.mark.asyncio
    async def test_verify_agent_token_happy_path(self, agent_token_service: AgentTokenService) -> None:
        from flycanon.web.controllers.agent._helpers import verify_agent_token

        token = await _mint(agent_token_service, scopes=["agent.sources:read"])
        ctx = await verify_agent_token(
            _request(token),
            service=agent_token_service,
            scope="agent.sources:read",
        )
        # The actor must be the verified agent actor, not the raw header
        # prefix the conventions parser would have surfaced.
        assert ctx.actor is not None
        assert ctx.actor.startswith("agent:")
        assert ctx.tenant_id == _TENANT
        assert ctx.workspace_id == _WORKSPACE

    @pytest.mark.asyncio
    async def test_verify_agent_token_missing_header(self, agent_token_service: AgentTokenService) -> None:
        from flycanon.web.controllers.agent._helpers import verify_agent_token

        with pytest.raises(MissingAgentToken):
            await verify_agent_token(
                _request(),  # no X-Agent-Token
                service=agent_token_service,
                scope="agent.sources:read",
            )

    @pytest.mark.asyncio
    async def test_verify_agent_token_wildcard_scope(self, agent_token_service: AgentTokenService) -> None:
        """A token with the ``*`` scope unlocks every per-route scope."""
        from flycanon.web.controllers.agent._helpers import verify_agent_token

        token = await _mint(agent_token_service, scopes=["*"])
        for scope in (
            "agent.sources:ingest",
            "agent.sources:read",
            "agent.query:run",
            "agent.knowledge:read",
            "agent.candidates:propose",
        ):
            ctx = await verify_agent_token(
                _request(token),
                service=agent_token_service,
                scope=scope,
            )
            assert ctx.actor is not None
            assert ctx.actor.startswith("agent:")

    def test_require_idempotency_key_happy_path(self) -> None:
        from flycanon.web.controllers.agent._helpers import require_idempotency_key

        key = require_idempotency_key(_request("agt_x", idempotency_key="K1"))
        assert key == "K1"

    def test_require_idempotency_key_missing(self) -> None:
        from flycanon.web.controllers.agent._helpers import require_idempotency_key

        with pytest.raises(MissingIdempotencyKey):
            require_idempotency_key(_request("agt_x"))  # no Idempotency-Key


# ---------------------------------------------------------------------
# 1. POST /api/v1/agent/sources  +  GET /api/v1/agent/sources/{id}
# ---------------------------------------------------------------------


def _sources_controller(
    agent_token_service: AgentTokenService,
    *,
    commands: Any = None,
    queries: Any = None,
    idempotency_store: Any = None,
) -> AgentSourcesController:
    return AgentSourcesController(
        agent_token_service=agent_token_service,
        commands=commands or _command_bus(),
        queries=queries or _query_bus(),
        url_fetcher=AsyncMock(),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
    )


def _submit_source_payload() -> Any:
    """Minimal valid ``SubmitSourceJsonPayload`` (8-byte ASCII body)."""
    import base64

    from flycanon.web.controllers.sources_controller import SubmitSourceJsonPayload

    return SubmitSourceJsonPayload(
        content_base64=base64.b64encode(b"hi-canon").decode("ascii"),
        filename="hello.txt",
        content_type="text/plain",
    )


class TestAgentSourcesSubmit:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_submit_command(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
        commands = _command_bus(send_returns="source-stub")
        controller = _sources_controller(agent_token_service, commands=commands)

        result = await controller.submit(
            _request(token, idempotency_key="src-key-1"),
            _submit_source_payload(),
        )

        assert result == "source-stub"
        # Exactly one dispatch -- the agent surface delegates to the
        # user-tier command without any duplicated work.
        commands.send.assert_awaited_once()
        cmd = commands.send.await_args.args[0]
        assert cmd.tenant_id == _TENANT
        assert cmd.workspace_id == _WORKSPACE
        # The verified actor is threaded through the command so the
        # audit row carries ``agent:<prefix>`` instead of ``anonymous``.
        assert cmd.actor is not None
        assert cmd.actor.startswith("agent:")

    @pytest.mark.asyncio
    async def test_missing_agent_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _sources_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.submit(
                _request(idempotency_key="key"),
                _submit_source_payload(),
            )

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        # Token has the read scope only -- ingest must be refused.
        token = await _mint(agent_token_service, scopes=["agent.sources:read"])
        controller = _sources_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.submit(
                _request(token, idempotency_key="key"),
                _submit_source_payload(),
            )

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.sources:ingest"],
            workspace_allowlist=["ws-other"],  # NOT _WORKSPACE
        )
        controller = _sources_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.submit(
                _request(token, idempotency_key="key"),
                _submit_source_payload(),
            )

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_returns_400(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
        controller = _sources_controller(agent_token_service)
        with pytest.raises(MissingIdempotencyKey):
            await controller.submit(
                _request(token),  # no Idempotency-Key
                _submit_source_payload(),
            )


class TestAgentSourcesGet:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_get_query(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.sources:read"])
        queries = _query_bus(query_returns="source-stub")
        controller = _sources_controller(agent_token_service, queries=queries)

        result = await controller.get_source(_request(token), "src-1")
        assert result == "source-stub"
        queries.query.assert_awaited_once()
        q = queries.query.await_args.args[0]
        assert q.source_id == "src-1"
        assert q.tenant_id == _TENANT
        assert q.workspace_id == _WORKSPACE

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _sources_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.get_source(_request(), "src-1")

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.sources:ingest"])
        controller = _sources_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.get_source(_request(token), "src-1")

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.sources:read"],
            workspace_allowlist=["ws-other"],
        )
        controller = _sources_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.get_source(_request(token), "src-1")

    @pytest.mark.asyncio
    async def test_unknown_id_raises_resource_not_found(self, agent_token_service: AgentTokenService) -> None:
        from pyfly.kernel import ResourceNotFoundException

        token = await _mint(agent_token_service, scopes=["agent.sources:read"])
        queries = _query_bus(query_returns=None)  # not found
        controller = _sources_controller(agent_token_service, queries=queries)
        with pytest.raises(ResourceNotFoundException):
            await controller.get_source(_request(token), "src-missing")


# ---------------------------------------------------------------------
# 2. POST /api/v1/agent/query  +  /search  +  /query/stream
# ---------------------------------------------------------------------


def _query_controller(
    agent_token_service: AgentTokenService,
    *,
    queries: Any = None,
    answer_service: Any = None,
    idempotency_store: Any = None,
) -> AgentQueryController:
    return AgentQueryController(
        agent_token_service=agent_token_service,
        queries=queries or _query_bus(),
        answer_service=answer_service or AsyncMock(),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
    )


def _answer_request() -> Any:
    from flycanon.interfaces.dtos.query import AnswerRequest

    return AnswerRequest(question="What is the scope?", top_k=3)


def _search_request() -> Any:
    from flycanon.interfaces.dtos.query import SearchRequest

    return SearchRequest(query="scope", top_k=3)


class TestAgentQueryAnswer:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_answer_query(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        queries = _query_bus(query_returns="answer-stub")
        controller = _query_controller(agent_token_service, queries=queries)

        result = await controller.answer(
            _request(token, idempotency_key="q-key-1"),
            _answer_request(),
        )
        assert result == "answer-stub"
        queries.query.assert_awaited_once()
        q = queries.query.await_args.args[0]
        assert q.tenant_id == _TENANT
        assert q.workspace_id == _WORKSPACE

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _query_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.answer(
                _request(idempotency_key="key"),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.knowledge:read"])
        controller = _query_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.answer(
                _request(token, idempotency_key="key"),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.query:run"],
            workspace_allowlist=["ws-other"],
        )
        controller = _query_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.answer(
                _request(token, idempotency_key="key"),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_returns_400(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        controller = _query_controller(agent_token_service)
        with pytest.raises(MissingIdempotencyKey):
            await controller.answer(
                _request(token),  # no key
                _answer_request(),
            )


class TestAgentSearch:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_search_query(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        queries = _query_bus(query_returns="search-stub")
        controller = _query_controller(agent_token_service, queries=queries)

        result = await controller.search(
            _request(token, idempotency_key="s-key-1"),
            _search_request(),
        )
        assert result == "search-stub"
        queries.query.assert_awaited_once()
        q = queries.query.await_args.args[0]
        assert q.tenant_id == _TENANT
        assert q.workspace_id == _WORKSPACE

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _query_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.search(
                _request(idempotency_key="key"),
                _search_request(),
            )

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.sources:read"])
        controller = _query_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.search(
                _request(token, idempotency_key="key"),
                _search_request(),
            )

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.query:run"],
            workspace_allowlist=["ws-other"],
        )
        controller = _query_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.search(
                _request(token, idempotency_key="key"),
                _search_request(),
            )

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_returns_400(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        controller = _query_controller(agent_token_service)
        with pytest.raises(MissingIdempotencyKey):
            await controller.search(
                _request(token),
                _search_request(),
            )


class TestAgentQueryStream:
    """Cover the SSE endpoint -- auth + idempotency happen before
    the stream is opened so the failure modes are normal exceptions,
    not in-stream ``error`` frames."""

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _query_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.stream_answer(
                _request(idempotency_key="key"),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.knowledge:read"])
        controller = _query_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.stream_answer(
                _request(token, idempotency_key="key"),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.query:run"],
            workspace_allowlist=["ws-other"],
        )
        controller = _query_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.stream_answer(
                _request(token, idempotency_key="key"),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_returns_400(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        controller = _query_controller(agent_token_service)
        with pytest.raises(MissingIdempotencyKey):
            await controller.stream_answer(
                _request(token),
                _answer_request(),
            )

    @pytest.mark.asyncio
    async def test_happy_path_returns_streaming_response(
        self, agent_token_service: AgentTokenService
    ) -> None:
        """Verifies the streaming response carries the SSE content type.

        The hits + answer pipeline is mocked: the real generator is
        covered in the conversations/query suite. Here we only care
        that the auth gate ran and that a :class:`StreamingResponse`
        was returned with the right media type and headers.
        """
        from starlette.responses import StreamingResponse

        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        answer_service = AsyncMock()
        # The retrieval is awaited via ``self._answer._retrieval.search(...)``;
        # the generator iterates the returned ``.hits`` list. An empty hits
        # list short-circuits the loop so the test does not need a full
        # ``Hit`` factory.
        retrieval = AsyncMock()
        retrieval.hits = []
        answer_service._retrieval = AsyncMock()
        answer_service._retrieval.search = AsyncMock(return_value=retrieval)
        # The blocking answer call returns a stubbed :class:`AnswerResponse`.
        from flycanon.interfaces.dtos.query import AnswerResponse

        answer_service.answer = AsyncMock(
            return_value=AnswerResponse(
                answer="Stub answer.",
                citations=[],
                model="stub:model",
                elapsed_ms=12,
                no_answer=False,
            )
        )
        controller = _query_controller(
            agent_token_service,
            answer_service=answer_service,
        )

        response = await controller.stream_answer(
            _request(token, idempotency_key="key"),
            _answer_request(),
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"


# ---------------------------------------------------------------------
# 3. GET /api/v1/agent/knowledge/{id}  + /provenance
# ---------------------------------------------------------------------


def _knowledge_controller(
    agent_token_service: AgentTokenService,
    *,
    queries: Any = None,
) -> AgentKnowledgeController:
    return AgentKnowledgeController(
        agent_token_service=agent_token_service,
        queries=queries or _query_bus(),
    )


class TestAgentKnowledgeGet:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_get_query(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.knowledge:read"])
        queries = _query_bus(query_returns="knowledge-stub")
        controller = _knowledge_controller(agent_token_service, queries=queries)

        result = await controller.get_knowledge(_request(token), "ki-1")
        assert result == "knowledge-stub"
        queries.query.assert_awaited_once()
        q = queries.query.await_args.args[0]
        assert q.item_id == "ki-1"
        assert q.tenant_id == _TENANT
        assert q.workspace_id == _WORKSPACE

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.get_knowledge(_request(), "ki-1")

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.sources:read"])
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.get_knowledge(_request(token), "ki-1")

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.knowledge:read"],
            workspace_allowlist=["ws-other"],
        )
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.get_knowledge(_request(token), "ki-1")

    @pytest.mark.asyncio
    async def test_unknown_id_raises_resource_not_found(self, agent_token_service: AgentTokenService) -> None:
        from pyfly.kernel import ResourceNotFoundException

        token = await _mint(agent_token_service, scopes=["agent.knowledge:read"])
        queries = _query_bus(query_returns=None)
        controller = _knowledge_controller(agent_token_service, queries=queries)
        with pytest.raises(ResourceNotFoundException):
            await controller.get_knowledge(_request(token), "ki-missing")


class TestAgentKnowledgeProvenance:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_provenance_query(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(agent_token_service, scopes=["agent.knowledge:read"])
        queries = _query_bus(query_returns="prov-stub")
        controller = _knowledge_controller(agent_token_service, queries=queries)

        result = await controller.get_provenance(_request(token), "ki-1", 0)
        assert result == "prov-stub"
        queries.query.assert_awaited_once()
        q = queries.query.await_args.args[0]
        assert q.item_id == "ki-1"
        # ``version=0`` collapses to ``None`` so the handler resolves to
        # the current version, matching the user-tier surface.
        assert q.version is None

    @pytest.mark.asyncio
    async def test_provenance_with_explicit_version(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.knowledge:read"])
        queries = _query_bus(query_returns="prov-stub")
        controller = _knowledge_controller(agent_token_service, queries=queries)

        await controller.get_provenance(_request(token), "ki-1", 3)
        q = queries.query.await_args.args[0]
        assert q.version == 3

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.get_provenance(_request(), "ki-1", 0)

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.get_provenance(_request(token), "ki-1", 0)

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.knowledge:read"],
            workspace_allowlist=["ws-other"],
        )
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.get_provenance(_request(token), "ki-1", 0)


# ---------------------------------------------------------------------
# 4. POST /api/v1/agent/candidates:propose
# ---------------------------------------------------------------------


def _candidates_controller(
    agent_token_service: AgentTokenService,
    *,
    commands: Any = None,
    idempotency_store: Any = None,
) -> AgentCandidatesController:
    return AgentCandidatesController(
        agent_token_service=agent_token_service,
        commands=commands or _command_bus(),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
    )


def _propose_request() -> Any:
    from flycanon.interfaces.dtos.candidate import ProposeCandidateRequest

    return ProposeCandidateRequest(source_id="src-1")


class TestAgentCandidatesPropose:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_propose_command(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(agent_token_service, scopes=["agent.candidates:propose"])
        commands = _command_bus(send_returns=["cand-stub"])
        controller = _candidates_controller(agent_token_service, commands=commands)

        result = await controller.propose(
            _request(token, idempotency_key="cand-key-1"),
            _propose_request(),
        )
        assert result == ["cand-stub"]
        commands.send.assert_awaited_once()
        cmd = commands.send.await_args.args[0]
        assert cmd.tenant_id == _TENANT
        assert cmd.workspace_id == _WORKSPACE
        assert cmd.actor is not None
        assert cmd.actor.startswith("agent:")

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, agent_token_service: AgentTokenService) -> None:
        controller = _candidates_controller(agent_token_service)
        with pytest.raises(MissingAgentToken):
            await controller.propose(
                _request(idempotency_key="key"),
                _propose_request(),
            )

    @pytest.mark.asyncio
    async def test_wrong_scope_returns_403(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.query:run"])
        controller = _candidates_controller(agent_token_service)
        with pytest.raises(AgentScopeDenied):
            await controller.propose(
                _request(token, idempotency_key="key"),
                _propose_request(),
            )

    @pytest.mark.asyncio
    async def test_workspace_not_in_allowlist_returns_403(
        self, agent_token_service: AgentTokenService
    ) -> None:
        token = await _mint(
            agent_token_service,
            scopes=["agent.candidates:propose"],
            workspace_allowlist=["ws-other"],
        )
        controller = _candidates_controller(agent_token_service)
        with pytest.raises(AgentWorkspaceNotInAllowlist):
            await controller.propose(
                _request(token, idempotency_key="key"),
                _propose_request(),
            )

    @pytest.mark.asyncio
    async def test_missing_idempotency_key_returns_400(self, agent_token_service: AgentTokenService) -> None:
        token = await _mint(agent_token_service, scopes=["agent.candidates:propose"])
        controller = _candidates_controller(agent_token_service)
        with pytest.raises(MissingIdempotencyKey):
            await controller.propose(
                _request(token),
                _propose_request(),
            )


# ---------------------------------------------------------------------
# 5. Cross-cutting: token lifecycle (invalid / expired) on one route
# ---------------------------------------------------------------------


class TestAgentTokenLifecycle:
    """Token-shape and lifecycle errors propagate the same way on every
    route -- pinning them on a single route (the cheapest one to
    exercise) is sufficient because the helper is shared."""

    @pytest.mark.asyncio
    async def test_unknown_token_returns_403(self, agent_token_service: AgentTokenService) -> None:
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(InvalidAgentToken):
            await controller.get_knowledge(
                _request("agt_deadbeef_0123456789abcdef0123456789abcdef"),
                "ki-1",
            )

    @pytest.mark.asyncio
    async def test_malformed_token_returns_403(self, agent_token_service: AgentTokenService) -> None:
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(InvalidAgentToken):
            await controller.get_knowledge(_request("garbage"), "ki-1")

    @pytest.mark.asyncio
    async def test_expired_token_returns_403(self, agent_token_service: AgentTokenService) -> None:
        expired = await _mint(
            agent_token_service,
            scopes=["agent.knowledge:read"],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        controller = _knowledge_controller(agent_token_service)
        with pytest.raises(AgentTokenExpired):
            await controller.get_knowledge(_request(expired), "ki-1")
