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

"""Search + RAG-answer DTOs.

flycanon exposes two retrieval surfaces:

* **``POST /api/v1/search``** -- raw hybrid retrieval. BM25 (Postgres
  ``tsvector`` + GIN on ``canon_chunks``) is fused with dense-vector
  search (pgvector) via Reciprocal Rank Fusion
  (parameter ``k`` from :class:`CanonSettings.retrieval_rrf_k`); the
  result is a ranked list of :class:`Hit` rows with the original
  chunk content + source pointers + fused score. No LLM call.

* **``POST /api/v1/query``** -- grounded RAG answer. Runs the same
  hybrid retrieval as ``/search``, then asks the configured answer
  model to write an answer using ONLY the supplied chunks. Returns
  the answer plus the citation list (the chunks the model actually
  relied on, hydrated to full :class:`Hit` rows).

Both surfaces share the same filter model so callers can scope the
retrieval window by source id, knowledge item, domain, jurisdiction,
tag, or knowledge-status.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


class _Filters(BaseModel):
    """Filters shared by ``/search`` and ``/query``.

    All filters compose with ``AND``. Empty lists / nulls are
    no-ops. Filters are applied *post-retrieval*, so callers that
    expect aggressive scoping should also widen ``per_query_k`` to
    avoid starving the answer with too few candidates.
    """

    source_ids: list[str] | None = Field(
        default=None,
        description="Restrict retrieval to chunks of these sources.",
        examples=[["0b06a8e6-37e7-4f7b-a8f1-7e2a6f7e5d11"]],
    )
    knowledge_item_ids: list[str] | None = Field(
        default=None,
        description="Restrict to chunks cited by these knowledge items.",
    )
    domains: list[Domain] | None = Field(
        default=None,
        description="Restrict to chunks whose source belongs to one of these domains.",
        examples=[["process", "compliance"]],
    )
    jurisdictions: list[Jurisdiction] | None = Field(default=None)
    tags: list[str] | None = Field(default=None)
    statuses: list[KnowledgeStatus] | None = Field(
        default=None,
        description=(
            "Restrict to chunks belonging to knowledge items in these "
            "lifecycle states. Defaults to ``published`` items only "
            "when omitted upstream."
        ),
    )


class SearchRequest(_Filters):
    """Hybrid-retrieval request body.

    Carries the query string + retrieval knobs. The defaults track
    :class:`CanonSettings.retrieval_top_k` /
    :class:`CanonSettings.retrieval_per_query_k`.
    """

    query: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "The free-form query. The retriever runs the query "
            "verbatim against BM25 and against the dense-vector "
            "projection (after embedding via the configured "
            "embedding model). Use plain natural language; no "
            "operator-style boolean syntax is required."
        ),
        examples=["What does the document say about scope?"],
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=200,
        description=(
            "Maximum number of fused hits to return. The retriever "
            "widens internally by 3x for post-filter trimming, so "
            "this value is the final ceiling regardless of filters."
        ),
    )
    per_query_k: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description=(
            "Per-modality (BM25 / vector) result cap before RRF "
            "fusion. Defaults to ``settings.retrieval_per_query_k`` "
            "(30). Raise it when filters drop most candidates."
        ),
    )


class Hit(BaseModel):
    """Single retrieval hit.

    Returned by both ``/search`` (as the page-level list) and
    ``/query`` (as the citation list -- the chunks the answer model
    relied on).
    """

    chunk_id: str = Field(description="Stable id of the matched chunk.")
    source_id: str = Field(description="Id of the source the chunk belongs to.")
    source_filename: str | None = Field(
        default=None,
        description=(
            "Original filename the source was ingested as "
            "(``Manifiesto.pdf``, ``Cleargate.docx``, ...). Lets a UI "
            "render a human-readable label without a second "
            "``/api/v1/sources/{id}`` round-trip."
        ),
        examples=["Cleargate Business Idea.docx"],
    )
    source_title: str | None = Field(
        default=None,
        description=(
            "Caller-supplied ``metadata.title`` or, when absent, the "
            "extractor-derived title from the document's embedded "
            "metadata (PyMuPDF / python-docx / python-pptx). Use as "
            "the citation label in answer UIs."
        ),
        examples=["Cleargate Business Idea"],
    )
    source_kind: str | None = Field(
        default=None,
        description=(
            "``SourceKind`` value of the source the chunk came from "
            "(``pdf`` / ``docx`` / ``pptx`` / ``image`` / ...). Lets "
            "the caller pick a file-type icon without re-fetching."
        ),
        examples=["pdf"],
    )
    source_uri: str | None = Field(
        default=None,
        description=(
            "Original URI of the source when it was submitted via URL "
            "rather than inline bytes. ``null`` for inline uploads."
        ),
    )
    section_path: str | None = Field(
        default=None,
        description=(
            "Section breadcrumb derived from the document headings "
            "(``Scope > In scope``, ``9) Repositorio + Controlador "
            "WebFlux``, ...). Mirrors ``canon_chunks.section_path``."
        ),
        examples=["Scope > In scope"],
    )
    page: int | None = Field(
        default=None,
        ge=1,
        description=(
            "1-based page number when the chunk's source has paginated "
            "layout (PDF, PPTX). ``null`` otherwise."
        ),
        examples=[2],
    )
    knowledge_item_id: str | None = Field(
        default=None,
        description=(
            "Populated when the chunk is cited by a published "
            "knowledge version -- lets callers jump directly to "
            "``/api/v1/knowledge/{id}`` for the canonical record."
        ),
    )
    knowledge_version: int | None = Field(default=None)
    content: str = Field(
        description=(
            "Verbatim chunk text. Suitable for direct display in a "
            "UI; the caller does not need to re-fetch ``canon_chunks``."
        )
    )
    score: float = Field(
        description=(
            "Reciprocal Rank Fusion score. Combines BM25 + vector "
            "rankings using the standard ``1 / (k + rank)`` formula "
            "with ``k = settings.retrieval_rrf_k`` (default 60). "
            "Higher is better; absolute values are comparable across "
            "queries within a single request but not across requests "
            "with different ``per_query_k`` or filters."
        ),
        examples=[0.6184],
    )
    bm25_rank: int | None = Field(
        default=None,
        ge=1,
        description="Rank in the BM25 ranking when present, else None.",
    )
    vector_rank: int | None = Field(
        default=None,
        ge=1,
        description="Rank in the dense-vector ranking when present, else None.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Free-form chunk metadata projected from the corpus index. "
            "The structured fields ``source_filename`` / "
            "``source_title`` / ``source_kind`` / ``section_path`` / "
            "``page`` are exposed as top-level attributes; this dict "
            "carries everything else the loader emitted."
        ),
        examples=[{"source_kind": "pdf"}],
    )


class SearchResponse(BaseModel):
    hits: list[Hit] = Field(description="Fused hit list, highest score first.")
    elapsed_ms: int = Field(
        ge=0,
        description=(
            "Wall-clock time spent in the retrieval pipeline "
            "(embed query + BM25 + vector + RRF fuse + hydrate). "
            "Does not include controller serialisation."
        ),
    )


class AnswerRequest(_Filters):
    """RAG-answer request.

    The answerer fetches the top hits via the same hybrid retrieval
    used by ``/search``, then asks the configured answer model to
    write an answer using ONLY those chunks. The response carries
    the answer + the chunks the model actually cited; the model is
    prompted to set ``no_answer=true`` when the chunks don't support
    a confident answer rather than confabulate.
    """

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="The user's question. Plain natural language.",
        examples=["Summarise the scope section in three sentences."],
    )
    top_k: int = Field(
        default=8,
        ge=1,
        le=40,
        description=(
            "Number of retrieval hits passed to the answer model as "
            "grounding context. Larger values widen the available "
            "evidence but raise input tokens; the default is sized "
            "for sub-second answers on small corpora."
        ),
    )
    instructions: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "Optional system-level steering appended to the answer "
            "prompt. Use for caller-specific tone (``answer in "
            "Spanish``, ``use bullet points``) or workflow context."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Override the configured answer model "
            "(``<provider>:<model>``). Defaults to "
            "``FLYCANON_ANSWER_MODEL``. On primary failure the "
            "service falls back to "
            "``FLYCANON_ANSWER_FALLBACK_MODEL`` when configured."
        ),
        examples=["anthropic:claude-sonnet-4-6", "openai:gpt-4o"],
    )


class AnswerResponse(BaseModel):
    answer: str = Field(
        description=(
            "The grounded answer. Free-form text; the prompt "
            "instructs the model to use only the supplied chunks."
        )
    )
    citations: list[Hit] = Field(
        description=(
            "Chunks the model explicitly cited -- a subset of the "
            "retrieved top-k. Each :class:`Hit` is hydrated with the "
            "verbatim chunk content + the source pointer so callers "
            "render the citation badge without a follow-up call."
        )
    )
    model: str = Field(description="Provider:model identifier actually used (after fallback).")
    elapsed_ms: int = Field(ge=0)
    no_answer: bool = Field(
        default=False,
        description=(
            "True when the retrieval window did not contain a "
            "confident answer. Callers should treat the answer text "
            "as an explanatory note rather than a substantive answer."
        ),
    )
