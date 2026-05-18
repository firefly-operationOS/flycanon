# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :func:`flycanon.core.agents.build_agent`.

The builder is the single place where every flycanon stage that
talks to an LLM gets its output-token budget. We don't run the
real :class:`FireflyAgent` (that would require pulling in the
provider SDKs); we patch the class and assert on the constructor
arguments so we know the resolution + override semantics hold.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from flycanon.core.agents import build_agent, resolve_max_output_tokens


@pytest.fixture
def settings():
    """A minimal stub matching the fields ``build_agent`` reads."""
    return SimpleNamespace(
        agent_max_output_tokens=8192,
        consolidator_max_output_tokens=None,
        answer_max_output_tokens=None,
    )


class TestResolveMaxOutputTokens:
    def test_falls_back_to_global_default(self, settings):
        assert resolve_max_output_tokens(settings) == 8192

    def test_override_wins(self, settings):
        assert resolve_max_output_tokens(settings, override=12000) == 12000

    def test_zero_override_is_honored(self, settings):
        # Caller can cap to 0 (theoretically). The resolver doesn't
        # interpret semantics -- it just returns what was asked. The
        # FireflyAgent / provider layer is where 0 would be rejected.
        assert resolve_max_output_tokens(settings, override=0) == 0


class _StubOutput:
    pass


class TestBuildAgent:
    def test_threads_max_tokens_into_model_settings(self, settings):
        with patch("fireflyframework_agentic.agents.FireflyAgent") as mock_cls:
            build_agent(
                name="flycanon-test",
                model="anthropic:claude-sonnet-4-6",
                output_type=_StubOutput,
                instructions="hi",
                settings=settings,
            )
        assert mock_cls.call_count == 1
        call = mock_cls.call_args
        # FireflyAgent(name, model=..., model_settings=..., ...)
        assert call.kwargs["model_settings"] == {"max_tokens": 8192}
        assert call.kwargs["model"] == "anthropic:claude-sonnet-4-6"
        assert call.kwargs["auto_register"] is False

    def test_per_call_override_wins(self, settings):
        with patch("fireflyframework_agentic.agents.FireflyAgent") as mock_cls:
            build_agent(
                name="flycanon-test",
                model="m",
                output_type=_StubOutput,
                instructions="hi",
                settings=settings,
                max_output_tokens=16000,
            )
        assert mock_cls.call_args.kwargs["model_settings"] == {"max_tokens": 16000}

    def test_extra_settings_overrides_max_tokens(self, settings):
        # ``extra_settings`` wins on conflict so a stage can cap below
        # the global default (e.g. a single-token classifier).
        with patch("fireflyframework_agentic.agents.FireflyAgent") as mock_cls:
            build_agent(
                name="flycanon-classifier",
                model="m",
                output_type=_StubOutput,
                instructions="hi",
                settings=settings,
                extra_settings={"max_tokens": 64, "temperature": 0.0},
            )
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["model_settings"]["max_tokens"] == 64
        assert kwargs["model_settings"]["temperature"] == 0.0

    def test_missing_framework_raises_runtime_error(self, settings):
        with patch.dict("sys.modules", {"fireflyframework_agentic.agents": None}):
            with pytest.raises(RuntimeError, match="fireflyframework_agentic"):
                build_agent(
                    name="x",
                    model="m",
                    output_type=_StubOutput,
                    instructions="hi",
                    settings=settings,
                )
