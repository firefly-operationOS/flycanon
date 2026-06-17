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

"""Replay-dedup coverage for the agent-tier POST endpoints.

The agent surface persists every successful POST's response under
``(tenant_id, route_scope, idempotency_key)``. A replayed call with
the same triple returns the stored response without re-dispatching
the underlying command -- the gate that lets flyradar's canon
handoff retry safely.

Endpoint matrix (four POSTs participate; the SSE stream is
intentionally exempt and covered by ``test_agent_surface``):

* ``POST /api/v1/agent/sources``              -- scope
  ``agent.sources:ingest``.
* ``POST /api/v1/agent/query``                -- scope
  ``agent.query:run``.
* ``POST /api/v1/agent/search``               -- scope
  ``agent.search:run`` (auth scope is ``agent.query:run`` but the
  replay key is namespaced under the route-specific dedup scope so
  ``/query`` + ``/search`` reusing the same key do not collide).
* ``POST /api/v1/agent/candidates:propose``   -- scope
  ``agent.candidates:propose``.

Per-endpoint expectations:

1. First call with key X -- dispatches, returns the response.
2. Second call with same key X -- returns the SAME body, bus called
   exactly ONCE (the second call short-circuits via the store).
3. Different key Y -- separate dispatch (bus called twice).

Cross-cutting (driven on the candidates route, which is the
cheapest to fake):

* Cross-route bleed: same key on ``/sources`` vs
  ``/candidates:propose`` MUST resolve to distinct stored responses
  (the scope is part of the store key, not just the tenant).
* Cross-tenant bleed: same key from tenant A vs tenant B MUST
  resolve to distinct stored responses.
* TTL expiry: an entry past its ``expires_at`` is treated as
  missing -- the controller re-dispatches.

The controllers are driven directly with stub :class:`Request`
objects and mocked CQRS buses, identical to the ``test_agent_surface``
fixtures. The auth gate runs end-to-end against an in-memory
:class:`AgentTokenService`; the dedup behaviour is exercised
against a real :class:`InMemoryIdempotencyStore`.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from flycanon.core.services.auth.agent_token_service import (
    AgentTokenService,
    MintRequest,
)
from flycanon.interfaces.dtos.candidate import (
    CandidateRecord,
    ProposeCandidateRequest,
)
from flycanon.interfaces.dtos.query import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)
from flycanon.interfaces.dtos.source import SourceRecord
from flycanon.interfaces.enums import (
    CandidateStatus,
    Domain,
    Jurisdiction,
    SourceKind,
    SourceStatus,
)
from flycanon.web.controllers.agent.candidates_controller import (
    AgentCandidatesController,
)
from flycanon.web.controllers.agent.query_controller import AgentQueryController
from flycanon.web.controllers.agent.sources_controller import AgentSourcesController
from flycanon.web.controllers.sources_controller import SubmitSourceJsonPayload
from flycanon.web.conventions import (
    HEADER_IDEMPOTENCY_KEY,
    InMemoryIdempotencyStore,
)
from flycanon.web.conventions.idempotency import (
    DEFAULT_IDEMPOTENCY_TTL,
    IdempotencyEntry,
    IdempotencyKey,
)

# ---------------------------------------------------------------------
# Test doubles -- the same stub repository / request used by the
# original ``test_agent_surface`` suite. Inlining the small helpers
# keeps this file self-contained.
# ---------------------------------------------------------------------


class _InMemoryAgentTokenRepository:
    """Mirror of the production :class:`AgentTokenRepository` contract."""

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


_TENANT_A = "acme"
_TENANT_B = "bcorp"
_WORKSPACE = "ws-agent-test"


@pytest.fixture
def store() -> InMemoryIdempotencyStore:
    """A fresh in-memory store per test -- no cross-test bleed."""
    return InMemoryIdempotencyStore()


@pytest.fixture
def repo_a() -> _InMemoryAgentTokenRepository:
    return _InMemoryAgentTokenRepository()


@pytest.fixture
def repo_b() -> _InMemoryAgentTokenRepository:
    return _InMemoryAgentTokenRepository()


@pytest.fixture
def service_a(repo_a: _InMemoryAgentTokenRepository) -> AgentTokenService:
    return AgentTokenService(repo_a)


@pytest.fixture
def service_b(repo_b: _InMemoryAgentTokenRepository) -> AgentTokenService:
    return AgentTokenService(repo_b)


async def _mint(
    service: AgentTokenService,
    *,
    scopes: list[str],
    tenant: str = _TENANT_A,
) -> str:
    """Mint a fresh agent token; wildcard scope unlocks all routes."""
    minted = await service.mint(
        MintRequest(
            tenant_id=tenant,
            name="replay-test-token",
            workspace_allowlist=None,
            scopes=scopes,
            rate_limit_rpm=None,
            expires_at=None,
        ),
        actor="anonymous",
    )
    return minted.token


def _req(
    token: str,
    *,
    idempotency_key: str,
    tenant: str = _TENANT_A,
    workspace: str = _WORKSPACE,
) -> _StubRequest:
    return _StubRequest(
        {
            "X-Tenant-Id": tenant,
            "X-Workspace-Id": workspace,
            "X-Agent-Token": token,
            HEADER_IDEMPOTENCY_KEY: idempotency_key,
        }
    )


def _command_bus(send_returns: Any) -> AsyncMock:
    bus = AsyncMock()
    bus.send = AsyncMock(return_value=send_returns)
    return bus


def _query_bus(query_returns: Any) -> AsyncMock:
    bus = AsyncMock()
    bus.query = AsyncMock(return_value=query_returns)
    return bus


# ---------------------------------------------------------------------
# Real DTO factories -- the replay path round-trips via
# ``Model.model_validate(cached_body)`` so the bus stubs MUST return
# real pydantic instances, not sentinel strings.
# ---------------------------------------------------------------------


def _source_record(rid: str = "src-1") -> SourceRecord:
    now = datetime.now(UTC)
    return SourceRecord(
        id=rid,
        kind=SourceKind.text,
        status=SourceStatus.ingested,
        filename="hello.txt",
        uri=None,
        content_sha256="0" * 64,
        content_bytes=8,
        n_chunks=1,
        error_code=None,
        error_message=None,
        created_at=now,
        ingested_at=now,
        updated_at=now,
    )


def _answer_response() -> AnswerResponse:
    return AnswerResponse(
        answer="The canonical answer.",
        citations=[],
        model="stub:model",
        elapsed_ms=12,
        no_answer=False,
    )


def _search_response() -> SearchResponse:
    return SearchResponse(hits=[], elapsed_ms=7)


def _candidate_record(cid: str = "cand-1") -> CandidateRecord:
    return CandidateRecord(
        id=cid,
        status=CandidateStatus.proposed,
        source_id="src-1",
        title="A proposed statement",
        body="Body of the proposed canonical statement.",
        domain=Domain.process,
        jurisdiction=Jurisdiction.GLOBAL,
        created_at=datetime.now(UTC),
    )


def _submit_source_payload() -> SubmitSourceJsonPayload:
    return SubmitSourceJsonPayload(
        content_base64=base64.b64encode(b"hi-canon").decode("ascii"),
        filename="hello.txt",
        content_type="text/plain",
    )


# ---------------------------------------------------------------------
# 1. POST /api/v1/agent/sources -- replay dedup
# ---------------------------------------------------------------------


class TestAgentSourcesReplay:
    """``submit`` caches the :class:`SourceRecord` under the
    ``agent.sources:ingest`` scope. A second call with the same key
    returns the same record without re-dispatching the command."""

    @pytest.mark.asyncio
    async def test_same_key_returns_cached_response_without_redispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.sources:ingest"])
        record = _source_record("src-replay-1")
        commands = _command_bus(send_returns=record)
        controller = AgentSourcesController(
            agent_token_service=service_a,
            commands=commands,
            queries=_query_bus(None),
            url_fetcher=AsyncMock(),
            idempotency_store=store,
        )
        request_factory = lambda: _req(token, idempotency_key="src-K1")  # noqa: E731

        first = await controller.submit(request_factory(), _submit_source_payload())
        second = await controller.submit(request_factory(), _submit_source_payload())

        # The bus was hit exactly once across the two calls.
        commands.send.assert_awaited_once()
        # Both responses round-trip to the same wire shape.
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert second.id == "src-replay-1"

    @pytest.mark.asyncio
    async def test_different_key_triggers_separate_dispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.sources:ingest"])
        commands = _command_bus(send_returns=_source_record("src-K-distinct"))
        controller = AgentSourcesController(
            agent_token_service=service_a,
            commands=commands,
            queries=_query_bus(None),
            url_fetcher=AsyncMock(),
            idempotency_store=store,
        )

        await controller.submit(_req(token, idempotency_key="src-K1"), _submit_source_payload())
        await controller.submit(_req(token, idempotency_key="src-K2"), _submit_source_payload())

        # Different keys -> different cache slots -> two dispatches.
        assert commands.send.await_count == 2


# ---------------------------------------------------------------------
# 2. POST /api/v1/agent/query -- replay dedup
# ---------------------------------------------------------------------


class TestAgentQueryAnswerReplay:
    @pytest.mark.asyncio
    async def test_same_key_returns_cached_response_without_redispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.query:run"])
        response = _answer_response()
        queries = _query_bus(query_returns=response)
        controller = AgentQueryController(
            agent_token_service=service_a,
            queries=queries,
            answer_service=AsyncMock(),
            answer_dispatcher=AsyncMock(is_rag=False),
            idempotency_store=store,
        )

        first = await controller.answer(
            _req(token, idempotency_key="q-K1"), AnswerRequest(question="why?", top_k=3)
        )
        second = await controller.answer(
            _req(token, idempotency_key="q-K1"), AnswerRequest(question="why?", top_k=3)
        )

        queries.query.assert_awaited_once()
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert second.answer == "The canonical answer."

    @pytest.mark.asyncio
    async def test_different_key_triggers_separate_dispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.query:run"])
        queries = _query_bus(query_returns=_answer_response())
        controller = AgentQueryController(
            agent_token_service=service_a,
            queries=queries,
            answer_service=AsyncMock(),
            answer_dispatcher=AsyncMock(is_rag=False),
            idempotency_store=store,
        )

        await controller.answer(_req(token, idempotency_key="q-K1"), AnswerRequest(question="why?", top_k=3))
        await controller.answer(_req(token, idempotency_key="q-K2"), AnswerRequest(question="why?", top_k=3))

        assert queries.query.await_count == 2


# ---------------------------------------------------------------------
# 3. POST /api/v1/agent/search -- replay dedup
# ---------------------------------------------------------------------


class TestAgentSearchReplay:
    @pytest.mark.asyncio
    async def test_same_key_returns_cached_response_without_redispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.query:run"])
        queries = _query_bus(query_returns=_search_response())
        controller = AgentQueryController(
            agent_token_service=service_a,
            queries=queries,
            answer_service=AsyncMock(),
            answer_dispatcher=AsyncMock(),
            idempotency_store=store,
        )

        first = await controller.search(
            _req(token, idempotency_key="s-K1"), SearchRequest(query="x", top_k=3)
        )
        second = await controller.search(
            _req(token, idempotency_key="s-K1"), SearchRequest(query="x", top_k=3)
        )

        queries.query.assert_awaited_once()
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_different_key_triggers_separate_dispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.query:run"])
        queries = _query_bus(query_returns=_search_response())
        controller = AgentQueryController(
            agent_token_service=service_a,
            queries=queries,
            answer_service=AsyncMock(),
            answer_dispatcher=AsyncMock(),
            idempotency_store=store,
        )

        await controller.search(_req(token, idempotency_key="s-K1"), SearchRequest(query="x", top_k=3))
        await controller.search(_req(token, idempotency_key="s-K2"), SearchRequest(query="x", top_k=3))

        assert queries.query.await_count == 2


# ---------------------------------------------------------------------
# 4. POST /api/v1/agent/candidates:propose -- replay dedup
# ---------------------------------------------------------------------


class TestAgentCandidatesProposeReplay:
    @pytest.mark.asyncio
    async def test_same_key_returns_cached_list_without_redispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.candidates:propose"])
        records = [_candidate_record("cand-A"), _candidate_record("cand-B")]
        commands = _command_bus(send_returns=records)
        controller = AgentCandidatesController(
            agent_token_service=service_a,
            commands=commands,
            idempotency_store=store,
        )

        first = await controller.propose(
            _req(token, idempotency_key="cand-K1"),
            ProposeCandidateRequest(source_id="src-1"),
        )
        second = await controller.propose(
            _req(token, idempotency_key="cand-K1"),
            ProposeCandidateRequest(source_id="src-1"),
        )

        commands.send.assert_awaited_once()
        assert isinstance(first, list)
        assert isinstance(second, list)
        assert len(first) == len(second) == 2
        # The list round-trips with identical content (re-hydrated
        # from the stored JSON).
        first_ids = sorted(r.id for r in first)
        second_ids = sorted(r.id for r in second)
        assert first_ids == second_ids == ["cand-A", "cand-B"]

    @pytest.mark.asyncio
    async def test_different_key_triggers_separate_dispatch(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["agent.candidates:propose"])
        commands = _command_bus(send_returns=[_candidate_record()])
        controller = AgentCandidatesController(
            agent_token_service=service_a,
            commands=commands,
            idempotency_store=store,
        )

        await controller.propose(
            _req(token, idempotency_key="cand-K1"),
            ProposeCandidateRequest(source_id="src-1"),
        )
        await controller.propose(
            _req(token, idempotency_key="cand-K2"),
            ProposeCandidateRequest(source_id="src-1"),
        )
        assert commands.send.await_count == 2


# ---------------------------------------------------------------------
# 5. Cross-route bleed -- same key on /sources vs /candidates MUST
# resolve to distinct stored responses.
# ---------------------------------------------------------------------


class TestCrossRouteBleed:
    """A token with the ``*`` scope can hit two routes with the same
    idempotency key without their responses leaking across routes."""

    @pytest.mark.asyncio
    async def test_same_key_distinct_routes_no_bleed(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        token = await _mint(service_a, scopes=["*"])
        # Two completely different responses -- bleed would surface
        # as one route returning the other's response body.
        sources_commands = _command_bus(send_returns=_source_record("src-route-bleed"))
        candidates_commands = _command_bus(send_returns=[_candidate_record("cand-route-bleed")])
        sources_controller = AgentSourcesController(
            agent_token_service=service_a,
            commands=sources_commands,
            queries=_query_bus(None),
            url_fetcher=AsyncMock(),
            idempotency_store=store,
        )
        candidates_controller = AgentCandidatesController(
            agent_token_service=service_a,
            commands=candidates_commands,
            idempotency_store=store,
        )

        # Same key on both routes -- expectation: each route caches
        # its own response under its own scope.
        shared_key = "SHARED-KEY-XYZ"
        src = await sources_controller.submit(
            _req(token, idempotency_key=shared_key),
            _submit_source_payload(),
        )
        cands = await candidates_controller.propose(
            _req(token, idempotency_key=shared_key),
            ProposeCandidateRequest(source_id="src-1"),
        )
        assert src.id == "src-route-bleed"
        assert isinstance(cands, list)
        assert cands[0].id == "cand-route-bleed"

        # Replays return the route-specific cache, not the other
        # route's body.
        src2 = await sources_controller.submit(
            _req(token, idempotency_key=shared_key),
            _submit_source_payload(),
        )
        cands2 = await candidates_controller.propose(
            _req(token, idempotency_key=shared_key),
            ProposeCandidateRequest(source_id="src-1"),
        )
        assert src2.id == "src-route-bleed"
        assert cands2[0].id == "cand-route-bleed"
        # Each command bus fired once (no cross-pollination
        # forcing a redundant dispatch).
        sources_commands.send.assert_awaited_once()
        candidates_commands.send.assert_awaited_once()


# ---------------------------------------------------------------------
# 6. Cross-tenant bleed -- same key from two distinct tenants MUST
# resolve to distinct stored responses (the store is namespaced by
# tenant_id, not just by scope + key).
# ---------------------------------------------------------------------


class TestCrossTenantBleed:
    @pytest.mark.asyncio
    async def test_same_key_distinct_tenants_no_bleed(
        self,
        service_a: AgentTokenService,
        service_b: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        # Two separate token services -> two separate tenants.
        token_a = await _mint(service_a, scopes=["agent.candidates:propose"], tenant=_TENANT_A)
        token_b = await _mint(service_b, scopes=["agent.candidates:propose"], tenant=_TENANT_B)

        commands_a = _command_bus(send_returns=[_candidate_record("cand-tenant-a")])
        commands_b = _command_bus(send_returns=[_candidate_record("cand-tenant-b")])
        controller_a = AgentCandidatesController(
            agent_token_service=service_a,
            commands=commands_a,
            idempotency_store=store,
        )
        controller_b = AgentCandidatesController(
            agent_token_service=service_b,
            commands=commands_b,
            idempotency_store=store,
        )

        shared_key = "SHARED-KEY-TENANT"
        a_first = await controller_a.propose(
            _req(token_a, idempotency_key=shared_key, tenant=_TENANT_A),
            ProposeCandidateRequest(source_id="src-1"),
        )
        b_first = await controller_b.propose(
            _req(token_b, idempotency_key=shared_key, tenant=_TENANT_B),
            ProposeCandidateRequest(source_id="src-1"),
        )
        assert a_first[0].id == "cand-tenant-a"
        assert b_first[0].id == "cand-tenant-b"

        # Each tenant's replay returns its own cached body, not the
        # other tenant's.
        a_second = await controller_a.propose(
            _req(token_a, idempotency_key=shared_key, tenant=_TENANT_A),
            ProposeCandidateRequest(source_id="src-1"),
        )
        b_second = await controller_b.propose(
            _req(token_b, idempotency_key=shared_key, tenant=_TENANT_B),
            ProposeCandidateRequest(source_id="src-1"),
        )
        assert a_second[0].id == "cand-tenant-a"
        assert b_second[0].id == "cand-tenant-b"
        # Each tenant's bus fired exactly once.
        commands_a.send.assert_awaited_once()
        commands_b.send.assert_awaited_once()


# ---------------------------------------------------------------------
# 7. TTL expiry -- a stored entry past its expiry is treated as
# missing and the controller re-dispatches.
# ---------------------------------------------------------------------


class TestTTLExpiry:
    @pytest.mark.asyncio
    async def test_expired_entry_treated_as_missing(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        # Pre-populate the store with an already-expired entry under
        # the same (tenant, scope, key) the controller will look up.
        # The controller MUST re-dispatch (the entry is treated as
        # absent) rather than serve a stale cached response.
        scope = "agent.candidates:propose"
        key = "TTL-KEY"
        store._replays[(_TENANT_A, scope, key)] = IdempotencyEntry(  # noqa: SLF001
            tenant_id=_TENANT_A,
            workspace_id="",
            route=scope,
            key=IdempotencyKey(key),
            response_hash="",
            job_id=None,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            response_status=201,
            response_json=[{"id": "STALE", "other": "fields"}],
        )

        token = await _mint(service_a, scopes=[scope])
        commands = _command_bus(send_returns=[_candidate_record("cand-fresh")])
        controller = AgentCandidatesController(
            agent_token_service=service_a,
            commands=commands,
            idempotency_store=store,
        )

        result = await controller.propose(
            _req(token, idempotency_key=key),
            ProposeCandidateRequest(source_id="src-1"),
        )

        # The expired stale entry was NOT served -- the controller
        # dispatched and returned a fresh candidate row.
        commands.send.assert_awaited_once()
        assert result[0].id == "cand-fresh"

    @pytest.mark.asyncio
    async def test_fresh_entry_still_returned_within_ttl(
        self,
        service_a: AgentTokenService,
        store: InMemoryIdempotencyStore,
    ) -> None:
        """Negative control for the TTL test: an unexpired entry
        IS served (i.e. the TTL test isn't passing because the
        lookup is broken in general)."""
        scope = "agent.candidates:propose"
        key = "TTL-KEY-FRESH"
        # An entry that expires well in the future.
        store._replays[(_TENANT_A, scope, key)] = IdempotencyEntry(  # noqa: SLF001
            tenant_id=_TENANT_A,
            workspace_id="",
            route=scope,
            key=IdempotencyKey(key),
            response_hash="",
            job_id=None,
            expires_at=datetime.now(UTC) + DEFAULT_IDEMPOTENCY_TTL,
            response_status=201,
            response_json=[_candidate_record("cand-from-cache").model_dump(mode="json")],
        )

        token = await _mint(service_a, scopes=[scope])
        commands = _command_bus(send_returns=[_candidate_record("cand-fresh-dispatch")])
        controller = AgentCandidatesController(
            agent_token_service=service_a,
            commands=commands,
            idempotency_store=store,
        )

        result = await controller.propose(
            _req(token, idempotency_key=key),
            ProposeCandidateRequest(source_id="src-1"),
        )

        # Bus was NOT called -- the cached response was served.
        commands.send.assert_not_awaited()
        assert result[0].id == "cand-from-cache"


# ---------------------------------------------------------------------
# 8. Helper-level unit tests -- the two new helpers themselves.
# ---------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.asyncio
    async def test_check_idempotency_replay_returns_none_when_store_empty(
        self,
        store: InMemoryIdempotencyStore,
    ) -> None:
        from flycanon.web.controllers.agent._helpers import check_idempotency_replay

        request = _StubRequest(
            {
                "X-Tenant-Id": _TENANT_A,
                "X-Workspace-Id": _WORKSPACE,
                HEADER_IDEMPOTENCY_KEY: "NEW-KEY",
            }
        )
        result = await check_idempotency_replay(request, store, "agent.sources:ingest")
        assert result is None

    @pytest.mark.asyncio
    async def test_check_idempotency_replay_returns_stored_response(
        self,
        store: InMemoryIdempotencyStore,
    ) -> None:
        from flycanon.web.controllers.agent._helpers import check_idempotency_replay

        await store.record_response(
            tenant_id=_TENANT_A,
            route="agent.sources:ingest",
            key="REPLAY-KEY",
            status=201,
            json_body={"id": "src-from-cache"},
        )
        request = _StubRequest(
            {
                "X-Tenant-Id": _TENANT_A,
                "X-Workspace-Id": _WORKSPACE,
                HEADER_IDEMPOTENCY_KEY: "REPLAY-KEY",
            }
        )
        result = await check_idempotency_replay(request, store, "agent.sources:ingest")
        assert result is not None
        assert result.status == 201
        assert result.body == {"id": "src-from-cache"}

    @pytest.mark.asyncio
    async def test_store_idempotent_response_round_trips(
        self,
        store: InMemoryIdempotencyStore,
    ) -> None:
        from flycanon.web.controllers.agent._helpers import (
            check_idempotency_replay,
            store_idempotent_response,
        )

        request = _StubRequest(
            {
                "X-Tenant-Id": _TENANT_A,
                "X-Workspace-Id": _WORKSPACE,
                HEADER_IDEMPOTENCY_KEY: "RT-KEY",
            }
        )
        await store_idempotent_response(
            request,
            store,
            "agent.sources:ingest",
            status=201,
            response={"id": "rt", "value": 1},
        )
        result = await check_idempotency_replay(request, store, "agent.sources:ingest")
        assert result is not None
        assert result.status == 201
        assert result.body == {"id": "rt", "value": 1}
