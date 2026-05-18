# Copyright 2026 Firefly Software Solutions Inc
"""Chunking strategies.

The default :class:`ParagraphChunker` shards each :class:`Section`
along paragraph boundaries while keeping the chunk size below the
configured token budget. Token counts use a 4-character heuristic that
matches what the agentic framework uses by default; this is a good
proxy across English / Spanish / Portuguese / French / German.

Two invariants make the downstream code simpler:

1. **Section-bounded**: chunks never span two sections. The section
   path is the canonical citation anchor; mixing sections inside a
   chunk would muddy the provenance graph.
2. **Stable ordering**: chunks are emitted in section order, then in
   paragraph order. Re-running the pipeline against the same input
   produces the same ``(index_in_source, content)`` pairs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from flycanon.core.services.ingestion.loaders import LoadedDocument, Section


@dataclass(slots=True)
class Chunk:
    """One retrieval-grade fragment of a source."""

    content: str
    index_in_source: int = 0
    total_chunks: int = 1
    char_start: int = 0
    char_end: int = 0
    section_path: str | None = None
    page: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class Chunker(Protocol):
    """Sharding contract."""

    def chunk(self, document: LoadedDocument) -> list[Chunk]: ...


def _approx_tokens(text: str, *, tokens_per_char: float = 0.25) -> int:
    """Return an integer token estimate for ``text``.

    Four characters per token is the canonical genai-prices heuristic
    for European languages; sufficient for chunk-size budgeting where
    a 10-15% overshoot is fine.
    """
    return max(1, int(len(text) * tokens_per_char))


@dataclass(slots=True)
class ParagraphChunker:
    """Paragraph-bounded chunker with token budgeting and soft overlap.

    Behaviour:

    * Splits each section on blank lines into paragraphs.
    * Greedily packs paragraphs into the current chunk until adding
      another would exceed ``chunk_size_tokens``.
    * On overflow, optionally seeds the next chunk with the trailing
      ``overlap_tokens`` characters of the previous one so retrieval
      remains robust across boundaries.
    """

    chunk_size_tokens: int = 1200
    overlap_tokens: int = 150
    tokens_per_char: float = 0.25

    def chunk(self, document: LoadedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        global_char_offset = 0
        char_budget = max(1, int(self.chunk_size_tokens / self.tokens_per_char))
        overlap_chars = max(0, int(self.overlap_tokens / self.tokens_per_char))

        for section in document.sections:
            section_path = " > ".join(p for p in section.path if p) or None
            for piece in self._pack_section(section, char_budget, overlap_chars):
                start = global_char_offset
                end = start + len(piece)
                chunks.append(
                    Chunk(
                        content=piece,
                        char_start=start,
                        char_end=end,
                        section_path=section_path,
                        page=section.page,
                    )
                )
                global_char_offset = end + 1  # account for the join newline

        total = len(chunks)
        for idx, chunk in enumerate(chunks):
            chunk.index_in_source = idx
            chunk.total_chunks = total
        return chunks

    def _pack_section(
        self,
        section: Section,
        char_budget: int,
        overlap_chars: int,
    ) -> Iterable[str]:
        paragraphs = [p.strip() for p in section.body.split("\n\n") if p.strip()]
        if not paragraphs:
            # Single-line section: fall through to character-bounded slicing.
            for piece in self._split_by_chars(section.body.strip(), char_budget, overlap_chars):
                if piece:
                    yield piece
            return

        buf: list[str] = []
        buf_len = 0
        for paragraph in paragraphs:
            paragraph_len = len(paragraph)
            if paragraph_len > char_budget:
                # Flush the buffer, then split the oversized paragraph.
                if buf:
                    yield "\n\n".join(buf)
                    buf = []
                    buf_len = 0
                for piece in self._split_by_chars(paragraph, char_budget, overlap_chars):
                    yield piece
                continue
            # Adding this paragraph would overflow the budget; emit and seed
            # the next chunk with an overlap snippet.
            if buf_len + paragraph_len + 2 > char_budget and buf:
                yield "\n\n".join(buf)
                if overlap_chars and len(buf[-1]) > overlap_chars:
                    seed = buf[-1][-overlap_chars:]
                    buf = [seed, paragraph]
                    buf_len = len(seed) + paragraph_len + 2
                else:
                    buf = [paragraph]
                    buf_len = paragraph_len
                continue
            buf.append(paragraph)
            buf_len += paragraph_len + (2 if buf_len else 0)
        if buf:
            yield "\n\n".join(buf)

    @staticmethod
    def _split_by_chars(text: str, char_budget: int, overlap_chars: int) -> Iterable[str]:
        if not text:
            return
        i = 0
        while i < len(text):
            end = min(len(text), i + char_budget)
            yield text[i:end]
            if end == len(text):
                return
            i = max(0, end - overlap_chars)
            # Defensive: ensure forward progress when overlap >= budget.
            if i == 0:
                i = end


def build_default_chunker(*, chunk_size_tokens: int, overlap_tokens: int) -> Chunker:
    """Factory that the DI configuration uses to build the chunker bean."""
    return ParagraphChunker(
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=overlap_tokens,
    )
