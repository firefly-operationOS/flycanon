# Copyright 2026 Firefly Software Solutions Inc
"""Chunker invariants -- ordering, section boundaries, overlap."""

from __future__ import annotations

from flycanon.core.services.ingestion.chunker import ParagraphChunker
from flycanon.core.services.ingestion.loaders import LoadedDocument, Section


def _doc(sections: list[tuple[list[str], str]]) -> LoadedDocument:
    return LoadedDocument(
        sections=[Section(path=list(path), body=body, order=i) for i, (path, body) in enumerate(sections)]
    )


def test_chunker_emits_one_chunk_for_short_section():
    document = _doc([(["Scope"], "Short body line.")])
    chunks = ParagraphChunker(chunk_size_tokens=200, overlap_tokens=0).chunk(document)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.content.startswith("Short body line.")
    assert chunk.section_path == "Scope"
    assert chunk.total_chunks == 1
    assert chunk.index_in_source == 0


def test_chunker_respects_section_boundaries():
    # Two sections; both fit individually so we expect two chunks
    # with distinct section_path values.
    document = _doc(
        [
            (["A"], "alpha paragraph one"),
            (["B"], "bravo paragraph one"),
        ]
    )
    chunks = ParagraphChunker(chunk_size_tokens=200, overlap_tokens=0).chunk(document)
    assert [c.section_path for c in chunks] == ["A", "B"]
    assert chunks[0].index_in_source == 0
    assert chunks[1].index_in_source == 1
    assert all(c.total_chunks == 2 for c in chunks)


def test_chunker_splits_when_section_overflows_budget():
    # ``chunk_size_tokens=4`` with the 4-char/token heuristic gives a
    # 16-char char budget; the body is ~100 chars so we expect multiple
    # chunks even though it is a single section.
    body = " ".join(["alphabet"] * 20)
    document = _doc([(["S"], body)])
    chunks = ParagraphChunker(chunk_size_tokens=4, overlap_tokens=0).chunk(document)
    assert len(chunks) > 1
    # Stable ordering: every chunk has the same section path and the
    # indexes are 0..N-1.
    assert all(c.section_path == "S" for c in chunks)
    assert [c.index_in_source for c in chunks] == list(range(len(chunks)))
