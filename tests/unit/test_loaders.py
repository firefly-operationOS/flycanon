# Copyright 2026 Firefly Software Solutions Inc
"""Loader smoke tests."""

from __future__ import annotations

from pathlib import Path

from flycanon.core.services.ingestion.loaders import (
    HtmlLoader,
    MarkdownLoader,
    TextLoader,
)


def test_markdown_loader_recovers_section_hierarchy(fixtures_dir: Path):
    bytes_ = (fixtures_dir / "sample.md").read_bytes()
    doc = MarkdownLoader().load(bytes_)
    assert doc.sections
    paths = [section.path for section in doc.sections]
    # Sample.md uses ``# Title``, ``## Scope``, ``## Procedure``,
    # ``### Edge cases``. The H1 becomes the breadcrumb root.
    assert ["Sample Operational Document", "Scope"] in paths
    assert ["Sample Operational Document", "Procedure", "Edge cases"] in paths


def test_html_loader_picks_up_p_and_li_bodies():
    html = b"""
    <html>
      <head><title>Doc</title></head>
      <body>
        <h1>Top</h1>
        <p>Intro paragraph.</p>
        <h2>Subsection</h2>
        <ul>
          <li>One</li>
          <li>Two</li>
        </ul>
      </body>
    </html>
    """
    doc = HtmlLoader().load(html)
    assert doc.title == "Doc"
    bodies = "\n".join(s.body for s in doc.sections)
    assert "Intro paragraph." in bodies
    assert "One" in bodies
    assert any(section.path[-1] == "Subsection" for section in doc.sections)


def test_text_loader_collapses_to_one_section():
    doc = TextLoader().load("a single paragraph of plain text.")
    assert len(doc.sections) == 1
    assert doc.sections[0].body == "a single paragraph of plain text."
