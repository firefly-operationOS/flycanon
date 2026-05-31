# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for cost-event persistence + aggregation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from flycanon.models.entities.cost_event import CostEventRow


@pytest.fixture
def cost_repo(repositories):
    return repositories["cost"]


async def _seed(
    repo,
    *,
    agent_name: str = "flycanon-answerer",
    model: str = "anthropic:claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: str = "0.001500",
    actor: str = "u1",
    occurred_at: datetime | None = None,
    tenant_id: str = "default",
    workspace_id: str = "default",
):
    row = CostEventRow(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_name=agent_name,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=Decimal(cost_usd),
        actor=actor,
    )
    if occurred_at is not None:
        row.occurred_at = occurred_at
    return await repo.add(row)


class TestPersistence:
    @pytest.mark.asyncio
    async def test_add_then_listed(self, cost_repo):
        await _seed(cost_repo)
        events = await cost_repo.list_events(limit=10)
        assert len(events) == 1
        assert events[0].agent_name == "flycanon-answerer"


class TestAggregation:
    @pytest.mark.asyncio
    async def test_group_by_model_sums_cost(self, cost_repo):
        await _seed(cost_repo, model="anthropic:claude", cost_usd="0.010")
        await _seed(cost_repo, model="anthropic:claude", cost_usd="0.020")
        await _seed(cost_repo, model="openai:gpt-4o", cost_usd="0.005")
        rows = await cost_repo.aggregate(group_by=["model"])
        by_model = {r["model"]: r for r in rows}
        assert Decimal(by_model["anthropic:claude"]["cost_usd"]) == Decimal("0.030")
        assert Decimal(by_model["openai:gpt-4o"]["cost_usd"]) == Decimal("0.005")

    @pytest.mark.asyncio
    async def test_group_by_actor_filters(self, cost_repo):
        """The repo does not group by actor, and ``aggregate`` does
        not accept an ``actor`` kwarg. Aggregation partitions on tenant
        + workspace (the real scope keys). Verify the cross-tenant
        filter narrows correctly."""
        await _seed(cost_repo, actor="alice", cost_usd="0.05", tenant_id="t-a")
        await _seed(cost_repo, actor="bob", cost_usd="0.10", tenant_id="t-b")
        rows = await cost_repo.aggregate(group_by=["model"], tenant_id="t-a")
        assert len(rows) == 1
        assert Decimal(rows[0]["cost_usd"]) == Decimal("0.05")
