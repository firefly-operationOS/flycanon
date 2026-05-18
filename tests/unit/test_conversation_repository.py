# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the ConversationRepository basics."""

from __future__ import annotations

import pytest

from flycanon.models.entities.conversation import (
    ConversationRow,
    ConversationTurnRow,
)


@pytest.fixture
def conv_repo(repositories):
    return repositories["conversation"]


class TestConversationLifecycle:
    @pytest.mark.asyncio
    async def test_add_and_get_round_trip(self, conv_repo):
        row = await conv_repo.add(
            ConversationRow(
                id="conv-1", title="t", actor="u", model="anthropic:claude"
            )
        )
        fetched = await conv_repo.get("conv-1")
        assert fetched is not None
        assert fetched.title == "t"
        assert fetched.actor == "u"


class TestTurns:
    @pytest.mark.asyncio
    async def test_next_turn_index_starts_at_1(self, conv_repo):
        await conv_repo.add(ConversationRow(id="conv-1"))
        assert await conv_repo.next_turn_index("conv-1") == 1

    @pytest.mark.asyncio
    async def test_next_turn_index_increments(self, conv_repo):
        await conv_repo.add(ConversationRow(id="conv-1"))
        await conv_repo.add_turn(
            ConversationTurnRow(
                conversation_id="conv-1",
                turn_index=1,
                question="q1",
                answer="a1",
            )
        )
        assert await conv_repo.next_turn_index("conv-1") == 2

    @pytest.mark.asyncio
    async def test_list_turns_ordered_by_index(self, conv_repo):
        await conv_repo.add(ConversationRow(id="conv-1"))
        # Insert out of order to exercise the ORDER BY.
        await conv_repo.add_turn(
            ConversationTurnRow(
                conversation_id="conv-1", turn_index=2, question="q2"
            )
        )
        await conv_repo.add_turn(
            ConversationTurnRow(
                conversation_id="conv-1", turn_index=1, question="q1"
            )
        )
        turns = await conv_repo.list_turns("conv-1")
        assert [t.turn_index for t in turns] == [1, 2]
