# Copyright 2026 Firefly Software Solutions Inc
"""Search + RAG-answer endpoints under ``/api/v1``.

Two surfaces, one retrieval engine:

* ``POST /api/v1/search`` -- raw hybrid retrieval (BM25 + vector +
  RRF fusion). No LLM call. Sub-second on small corpora; the answer
  surface uses this as its grounding step.
* ``POST /api/v1/query`` -- RAG answer with citations. Runs the
  same retrieval, then asks the configured answer model to write
  an answer over the top hits. Returns the answer plus the citation
  list (only the chunks the model actually relied on).
"""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultQueryBus
from pyfly.web import Body, Valid, post_mapping, request_mapping

from flycanon.core.services.query.handlers import (
    AnswerKnowledgeQuery,
    SearchKnowledgeQuery,
)
from flycanon.interfaces.dtos.query import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)


@rest_controller
@request_mapping("/api/v1")
class QueryController:
    """Hybrid search + grounded RAG answering."""

    def __init__(self, queries: DefaultQueryBus) -> None:
        self._queries = queries

    @post_mapping("/search", tags=["Query"])
    async def search(self, request: Valid[Body[SearchRequest]]) -> SearchResponse:
        """Hybrid retrieval over the canon corpus.

        BM25 (SQLite FTS5) is fused with dense-vector search via
        Reciprocal Rank Fusion::

            score(chunk) = sum_over_modalities( 1 / (k + rank) )

        with ``k`` from ``FLYCANON_RETRIEVAL_RRF_K`` (default 60).
        The vector half uses the embedding provider configured in
        ``FLYCANON_EMBEDDING_MODEL``; the BM25 half uses the
        FTS5-indexed chunk content.

        Filters in the request body compose with ``AND`` and are
        applied *post-retrieval*; the retriever widens internally by
        3x (so ``top_k=10`` actually fetches 30 candidates) before
        trimming, but heavy filtering may still starve the result
        set -- raise ``per_query_k`` if so.

        Use this surface when:

        * you want to power your own answer / summarisation step,
        * you need the raw evidence (chunk content + citation
          metadata) without the answer-stage cost,
        * you're building a "show citations" UI that lets the user
          read the source verbatim.

        Returns :class:`SearchResponse` (always 200). Empty result
        sets return ``hits=[]``.
        """
        return await self._queries.query(SearchKnowledgeQuery(request=request))

    @post_mapping("/query", tags=["Query"])
    async def answer(self, request: Valid[Body[AnswerRequest]]) -> AnswerResponse:
        """Grounded RAG answer with explicit citations.

        Pipeline:

        1. Hybrid retrieval (same as ``/search``) over the
           configured filters; pulls ``top_k`` candidates (default 8).

        2. Renders the ``answer.yaml`` prompt with the question +
           the retrieved chunks + the optional ``instructions``
           steering, and calls :class:`FireflyAgent` against the
           configured answer model (``FLYCANON_ANSWER_MODEL`` or the
           per-request ``model`` override).

        3. On primary-model failure the answerer falls through to
           ``FLYCANON_ANSWER_FALLBACK_MODEL`` when configured. Both
           steps are wrapped in a circuit breaker so transient
           failures don't cascade.

        4. The model returns a structured :class:`AnswerOutput`
           (Pydantic) carrying the answer text + the chunk_ids it
           explicitly relied on. The handler hydrates those ids
           back to full :class:`Hit` rows so the caller gets one
           round-trip.

        Response semantics:

        * ``no_answer=true`` -- the retrieval did not contain
          enough evidence; the answer text explains what is
          missing. Callers should NOT display the answer verbatim
          in a high-stakes UI; surface "no canonical answer yet"
          instead.
        * ``no_answer=false`` -- the answer is grounded in the
          ``citations`` list. The citations are a *subset* of the
          retrieval (only the chunks the model actually cited).

        Use this surface when:

        * you want a final user-facing answer with citation badges,
        * you don't want to operate a separate LLM call from your
          backend,
        * you're prototyping a chatbot or a copilot integration.
        """
        return await self._queries.query(AnswerKnowledgeQuery(request=request))
