# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the enriched ``Hit`` DTO mapper.

The mapper promotes structured source-side fields out of the
``hit.metadata`` bag into top-level Hit attributes so SDK consumers
don't need a second ``GET /sources/{id}`` to render citation labels.
"""

from __future__ import annotations

from dataclasses import dataclass

from flycanon.core.services.query.search_service import _hit_dto


@dataclass
class _StubHit:
    chunk_id: str
    source_id: str
    knowledge_item_id: str | None
    knowledge_version: int | None
    content: str
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    metadata: dict[str, str]


def _make_stub(metadata: dict[str, str]) -> _StubHit:
    return _StubHit(
        chunk_id="ch-1",
        source_id="src-1",
        knowledge_item_id=None,
        knowledge_version=None,
        content="hello",
        score=0.42,
        bm25_rank=None,
        vector_rank=None,
        metadata=dict(metadata),
    )


class TestHitDtoMapper:
    def test_promotes_source_filename_to_top_level(self):
        h = _hit_dto(
            _make_stub(
                {
                    "source_filename": "Cleargate.docx",
                    "source_title": "Cleargate Business Idea",
                    "source_kind": "docx",
                    "section_path": "9) Repositorio",
                }
            )
        )
        assert h.source_filename == "Cleargate.docx"
        assert h.source_title == "Cleargate Business Idea"
        assert h.source_kind == "docx"
        assert h.section_path == "9) Repositorio"
        # Promoted fields are removed from the residual metadata bag.
        assert "source_filename" not in h.metadata
        assert "source_title" not in h.metadata
        assert "source_kind" not in h.metadata
        assert "section_path" not in h.metadata

    def test_page_parses_to_int(self):
        h = _hit_dto(_make_stub({"page": "5"}))
        assert h.page == 5
        assert "page" not in h.metadata

    def test_page_invalid_string_falls_back_to_none(self):
        h = _hit_dto(_make_stub({"page": "not-a-number"}))
        assert h.page is None

    def test_page_missing_is_none(self):
        h = _hit_dto(_make_stub({}))
        assert h.page is None
        assert h.source_filename is None
        assert h.source_title is None

    def test_residual_metadata_preserved_for_forward_compat(self):
        h = _hit_dto(
            _make_stub(
                {
                    "source_filename": "x.pdf",
                    "custom_facet": "experimental",
                }
            )
        )
        assert h.metadata == {"custom_facet": "experimental"}
        assert h.source_filename == "x.pdf"

    def test_empty_string_promoted_fields_become_none(self):
        # Internal hits sometimes carry empty-string placeholders for
        # missing fields; the mapper normalises those to None so the
        # SDK shape is consistent.
        h = _hit_dto(
            _make_stub(
                {
                    "source_filename": "",
                    "source_title": "",
                    "source_kind": "",
                    "source_uri": "",
                    "section_path": "",
                }
            )
        )
        assert h.source_filename is None
        assert h.source_title is None
        assert h.source_kind is None
        assert h.source_uri is None
        assert h.section_path is None
