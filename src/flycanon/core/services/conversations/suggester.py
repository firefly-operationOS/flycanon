# Copyright 2026 Firefly Software Solutions Inc
"""Suggested follow-up question generator.

Powers ``POST /api/v1/query/suggest`` -- chat UIs render the
output as quick-reply chips ("did you mean? / continue with?").
The LLM is fed the user's question + the last grounded answer
when available and emits up to N short follow-ups in the same
language.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from pyfly.container import service

from flycanon.config import CanonSettings
from flycanon.core.agents import build_agent

logger = logging.getLogger(__name__)


class _SuggestionsOutput(BaseModel):
    suggestions: list[str] = Field(default_factory=list, max_length=10)


@service
class QuestionSuggester:
    _SYSTEM_PROMPT = (
        "You are a chat assistant for an enterprise operational "
        "knowledge repository. Given the user's last question (and "
        "optionally the assistant's last answer), propose AT MOST N "
        "natural follow-up questions the user might ask next.\n\n"
        "Rules:\n"
        "- Each suggestion <= 120 chars, single sentence.\n"
        "- Stay on the topic implied by the question/answer pair.\n"
        "- Match the language of the user's question.\n"
        "- Avoid trivial restatements; suggest the NEXT thing.\n"
        "- Do not number them or add prefixes."
    )

    def __init__(self, settings: CanonSettings) -> None:
        self._settings = settings

    async def suggest(
        self,
        *,
        question: str,
        answer: str | None,
        n: int,
    ) -> list[str]:
        if n <= 0:
            return []
        agent = build_agent(
            name="flycanon-suggester",
            model=self._settings.answer_model,
            output_type=_SuggestionsOutput,
            instructions=self._SYSTEM_PROMPT,
            settings=self._settings,
            max_output_tokens=512,
        )
        user_msg = (
            f"User question: {question}\n\n"
            + (f"Assistant answer:\n{answer}\n\n" if answer else "")
            + f'Emit at most {n} follow-up question suggestions as JSON ``{{suggestions: ["..."]}}``.'
        )
        try:
            result = await agent.run(user_msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("suggester failed: %s", exc)
            return []
        output: Any = getattr(result, "output", result)
        if isinstance(output, _SuggestionsOutput):
            suggestions = list(output.suggestions)
        elif isinstance(output, dict):
            suggestions = list(output.get("suggestions") or [])
        else:
            return []
        return [s.strip() for s in suggestions if s and s.strip()][:n]
