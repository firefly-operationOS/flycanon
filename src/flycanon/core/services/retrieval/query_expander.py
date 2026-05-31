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

"""LLM-driven query expansion.

The pre-retrieval stage asks the answer model for N paraphrases
of the user query. Each paraphrase runs through the retriever
independently; the result lists are RRF-fused for higher recall
on queries that miss vocabulary used in the corpus
("data retention" misses chunks that say "record disposal" /
"data lifecycle").

Opt-in -- it costs one extra LLM call per query. Flip it on for
high-stakes corpora where missed hits hurt more than the
~1-2 second answer-latency tax.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from flycanon.config import CanonSettings
from flycanon.core.agents import build_agent

logger = logging.getLogger(__name__)


class _ExpansionOutput(BaseModel):
    """Structured output type the expander LLM is asked for."""

    queries: list[str] = Field(default_factory=list, max_length=10)


class QueryExpander:
    """Multi-query expander backed by a :class:`FireflyAgent` call."""

    _SYSTEM_PROMPT = (
        "You are a search-query rewriter for an enterprise operational "
        "knowledge repository. Given a user question, emit up to N "
        "alternative paraphrases that preserve the user's intent but "
        "use synonyms, related terminology, or different phrasings "
        "that may match documents the original question would miss.\n\n"
        "Rules:\n"
        "- ALWAYS include the original query verbatim as the first "
        "entry.\n"
        "- Each paraphrase must be a single sentence or noun phrase "
        "<= 200 chars.\n"
        "- Avoid trivial restatements; vary the vocabulary.\n"
        "- Stay strictly on the topic of the original query."
    )

    def __init__(self, *, model: str, settings: CanonSettings) -> None:
        self._model = model
        self._settings = settings

    async def expand(self, query: str, *, n: int) -> list[str]:
        """Return up to ``n`` query variants (always includes the original).

        On failure (LLM unreachable, structured-output rejection,
        ...), returns ``[query]`` so the retrieval flow degrades
        gracefully into a regular single-query search.
        """
        if n <= 1:
            return [query]
        agent = build_agent(
            name="flycanon-query-expander",
            model=self._model,
            output_type=_ExpansionOutput,
            instructions=self._SYSTEM_PROMPT,
            settings=self._settings,
            # The expander emits up to N short strings -- cap the
            # output tokens far below the global default so a stuck
            # call costs ~500 tokens, not 8k.
            max_output_tokens=512,
        )
        prompt = (
            f"Original query: {query}\n\n"
            f"Emit AT MOST {n} variants (original first). "
            'Return them as JSON ``{queries: ["..."]}``.'
        )
        try:
            result = await agent.run(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("query expansion failed; using original: %s", exc)
            return [query]
        output: Any = getattr(result, "output", result)
        queries: list[str]
        if isinstance(output, _ExpansionOutput):
            queries = list(output.queries)
        elif isinstance(output, dict):
            queries = list(output.get("queries") or [])
        else:
            logger.warning("query expansion returned unexpected type %s", type(output))
            return [query]
        # Always anchor on the original, dedupe + cap.
        seen: set[str] = set()
        final: list[str] = []
        for candidate in [query, *queries]:
            normalised = (candidate or "").strip()
            if not normalised:
                continue
            key = normalised.lower()
            if key in seen:
                continue
            seen.add(key)
            final.append(normalised)
            if len(final) >= n:
                break
        return final or [query]
