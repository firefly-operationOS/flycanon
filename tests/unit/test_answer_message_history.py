# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the ``message_history`` translation helper.

Exercises :func:`flycanon.core.services.query.answer_service._build_message_history`
end-to-end against pydantic-ai's ``ModelRequest`` / ``ModelResponse``
types. The end-to-end ``answer()`` flow is exercised live in the
docker stack -- this is the pure-function path that the
conversational layer relies on.
"""

from __future__ import annotations

from flycanon.core.services.query.answer_service import _build_message_history


class TestBuildMessageHistory:
    def test_empty_input_returns_none(self):
        assert _build_message_history(None) is None
        assert _build_message_history([]) is None

    def test_pairs_round_trip_through_pydantic_ai(self):
        history = _build_message_history([("hello", "hi"), ("what about Y?", "Y is ...")])
        assert history is not None
        assert len(history) == 4
        first_user = getattr(history[0], "parts", [])[0]
        first_assistant = getattr(history[1], "parts", [])[0]
        assert getattr(first_user, "content", None) == "hello"
        assert getattr(first_assistant, "content", None) == "hi"

    def test_blank_pairs_skipped(self):
        history = _build_message_history([("", ""), ("real q", "real a")])
        assert history is not None
        assert len(history) == 2
