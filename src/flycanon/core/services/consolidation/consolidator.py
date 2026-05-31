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

"""LLM proposer -- source chunks in, canonical candidates out.

The :class:`Consolidator` builds a :class:`FireflyAgent` over the
caller's model identifier, renders the consolidation prompt, and
asks the model to emit a structured :class:`ConsolidationOutput`
containing one or more :class:`CandidateProposal` objects with
chunk-anchored citations.

The proposer is deliberately stateless: it does not touch Postgres,
the corpus, or the audit log. Persistence is the
:class:`CandidateService`'s responsibility.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from flycanon.config import CanonSettings
from flycanon.core.agents import build_agent
from flycanon.core.services.consolidation.errors import ConsolidationError
from flycanon.core.services.consolidation.prompt_loader import PromptTemplate
from flycanon.interfaces.enums import Domain, Jurisdiction
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.source import SourceRow

logger = logging.getLogger(__name__)


class CitationProposal(BaseModel):
    """Single citation emitted by the proposer."""

    chunk_id: str = Field(description="Id of the chunk supporting the claim.")
    quote: str | None = Field(default=None, description="Verbatim excerpt from the chunk.")
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)


class CandidateProposal(BaseModel):
    """One canonical-knowledge proposal emitted by the LLM."""

    title: str = Field(min_length=1, max_length=512)
    summary: str | None = Field(default=None, max_length=4000)
    body: str = Field(min_length=1)
    domain: Domain
    jurisdiction: Jurisdiction = Field(default=Jurisdiction.GLOBAL)
    tags: list[str] = Field(default_factory=list)
    citations: list[CitationProposal] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=4000)


class ConsolidationOutput(BaseModel):
    """Structured output type the LLM is asked to produce."""

    candidates: list[CandidateProposal] = Field(default_factory=list)


class Consolidator:
    """LLM proposer driven by a YAML+Jinja2 prompt template."""

    def __init__(
        self,
        *,
        prompt: PromptTemplate,
        default_model: str,
        settings: CanonSettings,
    ) -> None:
        self._prompt = prompt
        self._default_model = default_model
        self._settings = settings

    async def propose(
        self,
        *,
        source: SourceRow,
        chunks: Sequence[KnowledgeChunkRow],
        domain_hint: Domain | None = None,
        jurisdiction_hint: Jurisdiction | None = None,
        extra_instructions: str | None = None,
        model: str | None = None,
    ) -> ConsolidationOutput:
        """Run the proposer and return a structured candidate set."""
        if not chunks:
            return ConsolidationOutput()

        metadata = dict(source.metadata_json or {})
        source_title = metadata.get("title") or source.filename or source.uri or source.id
        system_text, user_text = self._prompt.render(
            source_title=source_title,
            source_kind=source.kind,
            domain_hint=domain_hint.value if domain_hint else None,
            jurisdiction_hint=jurisdiction_hint.value if jurisdiction_hint else None,
            extra_instructions=extra_instructions,
            chunks=[
                {
                    "id": chunk.id,
                    "section_path": chunk.section_path,
                    "page": chunk.page,
                    "content": chunk.content,
                }
                for chunk in chunks
            ],
            domain_values=[d.value for d in Domain],
            jurisdiction_values=[j.value for j in Jurisdiction],
        )

        agent = self._build_agent(system_text, model)
        try:
            result = await agent.run(user_text)
        except Exception as exc:
            raise ConsolidationError(f"consolidation LLM call failed: {exc}") from exc
        output = self._extract_output(result)

        # Defensive: drop citations that reference chunks outside the
        # provided window. The LLM occasionally hallucinates a chunk
        # id; we'd rather ship fewer citations than wrong ones.
        valid_ids = {chunk.id for chunk in chunks}
        for candidate in output.candidates:
            candidate.citations = [c for c in candidate.citations if c.chunk_id in valid_ids]
        logger.info(
            "consolidation source=%s chunks=%d candidates=%d",
            source.id,
            len(chunks),
            len(output.candidates),
        )
        return output

    def _build_agent(self, system_text: str, model: str | None) -> Any:
        # Per-stage override pulled from ``consolidator_max_output_tokens``
        # when set, else the global ``agent_max_output_tokens`` default
        # (8192). The consolidator is the most likely stage to need a
        # larger budget -- dense business documents routinely produce
        # multi-candidate arrays that overflow the 4096 provider default.
        try:
            return build_agent(
                name="flycanon-consolidator",
                model=model or self._default_model,
                output_type=ConsolidationOutput,
                instructions=system_text,
                settings=self._settings,
                max_output_tokens=self._settings.consolidator_max_output_tokens,
            )
        except RuntimeError as exc:
            raise ConsolidationError(str(exc)) from exc

    @staticmethod
    def _extract_output(result: Any) -> ConsolidationOutput:
        """Coerce the agent's response into a :class:`ConsolidationOutput`.

        FireflyAgent's response object exposes the structured payload
        via ``output``; we accept either a model instance or a dict
        (some providers return the raw JSON before validation).
        """
        output = getattr(result, "output", result)
        if isinstance(output, ConsolidationOutput):
            return output
        if isinstance(output, dict):
            return ConsolidationOutput.model_validate(output)
        if isinstance(output, str):
            # Fallback: model returned a JSON string.
            return ConsolidationOutput.model_validate_json(output)
        raise ConsolidationError(f"unexpected consolidation output type {type(output).__name__}")
