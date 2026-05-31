# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the billing drill-down surface on :class:`CostService`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from flycanon.core.services.billing import CostService


@pytest.fixture
def cost_service(repositories):
    return CostService(repositories["cost"])


async def _seed_call(
    service: CostService,
    *,
    agent_name: str = "flycanon-answerer",
    model: str = "anthropic:claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: str = "0.001500",
    actor: str = "alice",
    latency_ms: int | None = 250,
    subject_kind: str | None = None,
    subject_id: str | None = None,
    tenant_id: str = "default",
    workspace_id: str = "default",
):
    return await service.record(
        agent_name=agent_name,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=Decimal(cost_usd),
        actor=actor,
        latency_ms=latency_ms,
        subject_kind=subject_kind,
        subject_id=subject_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


class TestListEvents:
    @pytest.mark.asyncio
    async def test_returns_recently_recorded_calls(self, cost_service):
        await _seed_call(cost_service, actor="alice")
        await _seed_call(cost_service, actor="bob")
        rows = await cost_service.list_events(limit=10)
        assert {r.actor for r in rows} == {"alice", "bob"}

    @pytest.mark.asyncio
    async def test_actor_filter_narrows(self, cost_service):
        """``list_events`` narrows by tenant + workspace (the real
        scope keys), not by ``actor`` (audit metadata). Verify the rows
        are partitioned by the right tenant_id."""
        await _seed_call(cost_service, actor="alice", tenant_id="t-a")
        await _seed_call(cost_service, actor="bob", tenant_id="t-b")
        rows = await cost_service.list_events(tenant_id="t-a", limit=10)
        assert len(rows) == 1
        assert rows[0].actor == "alice"


class TestSummary:
    @pytest.mark.asyncio
    async def test_buckets_populated_with_zero_when_empty(self, cost_service):
        snapshot = await cost_service.summary()
        for bucket in ("last_24h", "last_7d", "last_30d"):
            assert snapshot[bucket]["calls"] == 0
            assert snapshot[bucket]["cost_usd"] == "0"

    @pytest.mark.asyncio
    async def test_top_model_and_actor_picked_in_window(self, cost_service):
        """``summary`` returns the top model only; there is no
        ``top_actor`` field."""
        await _seed_call(cost_service, model="openai:gpt-4o", cost_usd="0.10", actor="alice")
        await _seed_call(cost_service, model="anthropic:claude", cost_usd="0.50", actor="bob")
        snapshot = await cost_service.summary()
        assert snapshot["last_24h"]["top_model"] == "anthropic:claude"
        # ``top_actor`` is not surfaced -- actor is audit metadata,
        # not a partitioning key.
        assert "top_actor" not in snapshot["last_24h"]

    @pytest.mark.asyncio
    async def test_actor_filter_omits_top_actor(self, cost_service):
        """The ``summary`` API does not accept ``actor``; scope flows
        from tenant + workspace headers. ``top_actor`` is never
        returned."""
        await _seed_call(cost_service, cost_usd="0.10", actor="alice", tenant_id="t-a")
        snapshot = await cost_service.summary(tenant_id="t-a")
        assert "top_actor" not in snapshot["last_24h"]


class TestTop:
    @pytest.mark.asyncio
    async def test_top_model_sorted_desc(self, cost_service):
        await _seed_call(cost_service, model="m1", cost_usd="0.01")
        await _seed_call(cost_service, model="m2", cost_usd="0.10")
        await _seed_call(cost_service, model="m3", cost_usd="0.50")
        rows = await cost_service.top(dimension="model", limit=2)
        assert [r["model"] for r in rows] == ["m3", "m2"]

    @pytest.mark.asyncio
    async def test_unknown_dimension_rejected(self, cost_service):
        with pytest.raises(ValueError):
            await cost_service.top(dimension="garbage")


class TestBySubject:
    @pytest.mark.asyncio
    async def test_groups_per_subject(self, cost_service):
        await _seed_call(
            cost_service,
            subject_kind="source",
            subject_id="src-1",
            cost_usd="0.10",
        )
        await _seed_call(
            cost_service,
            subject_kind="source",
            subject_id="src-1",
            cost_usd="0.05",
        )
        await _seed_call(
            cost_service,
            subject_kind="source",
            subject_id="src-2",
            cost_usd="0.07",
        )
        rows = await cost_service.by_subject(subject_kind="source")
        by_id = {r["subject_id"]: r for r in rows}
        assert Decimal(by_id["src-1"]["cost_usd"]) == Decimal("0.15")
        assert Decimal(by_id["src-2"]["cost_usd"]) == Decimal("0.07")

    @pytest.mark.asyncio
    async def test_subjectless_calls_excluded(self, cost_service):
        await _seed_call(cost_service, cost_usd="0.01")
        rows = await cost_service.by_subject()
        assert rows == []


class TestLatency:
    @pytest.mark.asyncio
    async def test_percentiles_per_model(self, cost_service):
        for ms in (100, 200, 300, 400, 500):
            await _seed_call(cost_service, model="m-fast", latency_ms=ms)
        for ms in (1000, 2000, 3000):
            await _seed_call(cost_service, model="m-slow", latency_ms=ms)
        rows = await cost_service.latency(group_by=["model"])
        by_model = {r["model"]: r for r in rows}
        assert by_model["m-fast"]["count"] == 5
        assert by_model["m-fast"]["p50_ms"] == 300
        assert by_model["m-fast"]["max_ms"] == 500
        assert by_model["m-slow"]["count"] == 3
        assert by_model["m-slow"]["max_ms"] == 3000

    @pytest.mark.asyncio
    async def test_null_latency_excluded(self, cost_service):
        await _seed_call(cost_service, model="m1", latency_ms=None)
        rows = await cost_service.latency(group_by=["model"])
        assert rows == []


class TestWindowedFilters:
    @pytest.mark.asyncio
    async def test_aggregate_respects_since(self, cost_service):
        old = await _seed_call(cost_service, cost_usd="0.10")
        # Force the timestamp into the past via the repo so the
        # ``since`` filter has something to elide.
        async with cost_service._repository.session() as session:
            stored = await session.get(type(old), old.id)
            stored.occurred_at = datetime.now(UTC) - timedelta(days=2)
        await _seed_call(cost_service, cost_usd="0.20")  # recent
        since = datetime.now(UTC) - timedelta(hours=1)
        rows = await cost_service.aggregate(group_by=["model"], since=since)
        assert len(rows) == 1
        assert Decimal(rows[0]["cost_usd"]) == Decimal("0.20")
