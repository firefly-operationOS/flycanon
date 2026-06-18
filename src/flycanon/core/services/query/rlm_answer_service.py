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

"""RLM answerer -- whole-document corpus + CodeAct REPL with citations.

Drop-in alternative to the RAG :class:`AnswerService`: same ``answer()``
contract (``AnswerRequest`` in, ``AnswerResponse`` out), so the query
dispatcher can swap one for the other. Instead of hybrid retrieval +
single grounded LLM call, it builds the whole-document corpus
(:class:`CanonCorpusBuilder`), runs the Recursive Language Model engine
(:class:`RLMSession` driven by :class:`AnthropicClient`) inside
``asyncio.to_thread`` so the synchronous engine never blocks the event
loop, then maps the engine's citation dicts back to :class:`Hit` rows
via :meth:`CanonDocStore.resolve`.

Engine citations are ``{"filing": key, "page": int (0-based), "content":
snippet}``; :class:`Hit.page` is 1-based, so pages are shifted by one and
clamped to ``>= 1``. Citations whose ``filing`` key no longer resolves to
a source are dropped.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from flycanon.config import CanonSettings
from flycanon.core.services.billing.cost_service import CostService
from flycanon.core.services.query.rlm.client import AnthropicClient
from flycanon.core.services.query.rlm.corpus import CanonCorpusBuilder, CanonDocStore, Filters
from flycanon.core.services.query.rlm.session import RLMSession
from flycanon.interfaces.dtos.query import AnswerRequest, AnswerResponse, Hit

logger = logging.getLogger(__name__)

# The not-found sentinels the engine emits when the evidence is absent
# (see the ``final("the documents do not contain this", ...)`` guidance in
# the RLM system prompt). Used -- alongside an empty citation list -- as a
# fallback for deciding ``no_answer`` when the engine's structured
# ``found=False`` flag is not set.
_NOT_FOUND_MARKERS = ("do not contain", "not found", "no documents", "cannot find")

# Cap on how many prior turns we fold into the question for conversational
# parity, and how much of each we keep -- bounded so a long history can't
# blow up the orchestrator's first prompt.
_MAX_PRIOR_TURNS = 6
_PRIOR_TURN_CHARS = 500


class RLMAnswerService:
    """RLM answerer mirroring the RAG :class:`AnswerService` interface.

    ``corpus_builder`` / ``client`` are injected so tests can supply a
    fake corpus and a fake (non-networked) Anthropic client.
    """

    def __init__(
        self,
        *,
        corpus_builder: CanonCorpusBuilder,
        client: AnthropicClient,
        settings: CanonSettings,
        cost_service: CostService,
    ) -> None:
        self._corpus_builder = corpus_builder
        self._client = client
        self._settings = settings
        self._cost_service = cost_service

    async def answer(
        self,
        request: AnswerRequest,
        *,
        prior_turns: list[tuple[str, str]] | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        on_turn: Callable[[int, list[str]], None] | None = None,
    ) -> AnswerResponse:
        """Single-turn (or conversational) RLM answer.

        Mirrors :meth:`AnswerService.answer`: ``tenant_id`` /
        ``workspace_id`` are the authoritative scope (the corpus builder
        lists the workspace's sources under them), and ``prior_turns`` is
        an optional ``[(user_text, assistant_text), ...]`` conversational
        context. The reported ``model`` is the RLM orchestrator model
        (``settings.rlm_root_model``), which drives the CodeAct loop.

        ``on_turn`` is an optional per-REPL-turn progress hook the
        streaming controllers pass to surface live progress; it stays
        ``None`` on the non-streaming path. It fires from the engine's
        worker thread, so the caller is responsible for marshalling work
        back onto its event loop.
        """
        start = time.perf_counter()
        model_id = self._settings.rlm_root_model

        docs = await self._corpus_builder.build(
            tenant_id=tenant_id or "",
            workspace_id=workspace_id or "",
            filters=_filters_from_request(request),
        )
        if len(docs) == 0:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return AnswerResponse(
                answer=("There are no documents in the canon that support an answer to that question."),
                citations=[],
                model=model_id,
                elapsed_ms=elapsed_ms,
                no_answer=True,
            )

        # Fork the shared client so this query gets its own token tally
        # (while reusing the connection pool); the singleton's counters
        # would otherwise cross-contaminate cost between concurrent queries.
        query_client = self._client.fork()
        session = RLMSession(
            query_client,
            max_depth=self._settings.rlm_max_depth,
            max_iters=self._settings.rlm_max_iters,
            sub_budget=self._settings.rlm_sub_budget,
            on_turn=on_turn,
        )
        question = _question_with_history(request.question, prior_turns)
        answer_text, cites, engine_no_answer = await asyncio.to_thread(session.run, question, docs)

        citations = _map_citations(cites, docs)
        # Trust the engine's structured no-answer flag first; keep the
        # text-marker check as a fallback for models that answer in plain text
        # without passing ``found=False``.
        no_answer = engine_no_answer or (not citations and _looks_not_found(answer_text))

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        usage = query_client.token_totals()
        await self._record_cost(usage, elapsed_ms, tenant_id, workspace_id)
        logger.info(
            "rlm answered question=%s docs=%d citations=%d elapsed_ms=%d",
            request.question[:80],
            len(docs),
            len(citations),
            elapsed_ms,
        )
        return AnswerResponse(
            answer=answer_text,
            citations=citations,
            model=model_id,
            elapsed_ms=elapsed_ms,
            no_answer=no_answer,
        )

    async def _record_cost(
        self,
        usage: dict,
        elapsed_ms: int,
        tenant_id: str | None,
        workspace_id: str | None,
    ) -> None:
        """Best-effort cost record for one RLM answer query.

        A billing-write failure must never fail the user's answer, so the
        record call is wrapped and only logged on error.
        """
        try:
            await self._cost_service.record(
                agent_name="flycanon-rlm-answerer",
                model=self._settings.rlm_root_model,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost_usd=usage["estimated_cost_usd"],
                tenant_id=tenant_id or "default",
                workspace_id=workspace_id or "default",
                latency_ms=elapsed_ms,
            )
        except Exception:  # noqa: BLE001 -- billing must not break the answer
            logger.warning("failed to record RLM query cost", exc_info=True)


def _filters_from_request(request: AnswerRequest) -> Filters:
    """Map the ``AnswerRequest`` filter fields onto the corpus ``Filters``.

    Enum-valued dimensions are passed as their ``.value`` strings, the
    same way the RAG search service hands plain strings to the retrieval
    filter.
    """
    return Filters(
        source_ids=request.source_ids,
        knowledge_item_ids=request.knowledge_item_ids,
        domains=[d.value for d in request.domains] if request.domains else None,
        jurisdictions=[j.value for j in request.jurisdictions] if request.jurisdictions else None,
        tags=request.tags,
        statuses=[s.value for s in request.statuses] if request.statuses else None,
    )


def _map_citations(cites: list[dict], docs: CanonDocStore) -> list[Hit]:
    """Turn engine citation dicts into hydrated :class:`Hit` rows.

    Each engine citation is ``{"filing": key, "page": int (0-based),
    "content": snippet}``. The readable ``filing`` key is resolved back to
    its source via :meth:`CanonDocStore.resolve`; citations whose key no
    longer resolves are dropped. The 0-based engine page becomes a 1-based
    :class:`Hit.page` clamped to ``>= 1``, and a stable synthetic
    ``chunk_id`` is synthesised from the source id + page.
    """
    citations: list[Hit] = []
    for cite in cites:
        key = cite.get("filing")
        if not isinstance(key, str):
            continue
        meta = docs.resolve(key)
        if meta is None:
            continue
        page = max(1, int(cite.get("page", 0)) + 1)
        citations.append(
            Hit(
                chunk_id=f"{meta.source_id}#p{page}",
                source_id=meta.source_id,
                source_filename=meta.filename,
                source_title=meta.title,
                source_kind=meta.kind,
                page=page,
                content=str(cite.get("content", "")),
                score=1.0,
                section_path=None,
                metadata={},
            )
        )
    return citations


def _question_with_history(
    question: str,
    prior_turns: list[tuple[str, str]] | None,
) -> str:
    """Prepend a short, bounded ``Previous turns:`` block to the question.

    Conversational parity with the RAG path: when ``prior_turns`` are
    present, the most recent turns are folded into the question the
    orchestrator sees. Empty / ``None`` history leaves the question
    untouched.
    """
    if not prior_turns:
        return question
    lines: list[str] = []
    for user_text, assistant_text in prior_turns[-_MAX_PRIOR_TURNS:]:
        if not user_text and not assistant_text:
            continue
        lines.append(f"User: {(user_text or '')[:_PRIOR_TURN_CHARS]}")
        lines.append(f"Assistant: {(assistant_text or '')[:_PRIOR_TURN_CHARS]}")
    if not lines:
        return question
    history = "\n".join(lines)
    return f"Previous turns:\n{history}\n\nCurrent question: {question}"


def _looks_not_found(answer: str) -> bool:
    """Whether the answer text matches an engine not-found sentinel."""
    lowered = answer.lower()
    return any(marker in lowered for marker in _NOT_FOUND_MARKERS)
