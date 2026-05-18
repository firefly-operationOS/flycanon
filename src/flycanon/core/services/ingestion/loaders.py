# Copyright 2026 Firefly Software Solutions Inc
"""Source loaders -- bytes in, :class:`LoadedDocument` out.

Each loader implements :class:`SourceLoader` and is responsible for
producing a normalised representation of the source: a list of
:class:`Section` objects carrying the heading hierarchy, the verbatim
text under each section, and a small bit of positional metadata
(``page``, ``order``) the chunker uses to attach context to each
fragment.

The DOCX loader is the canonical reference: it walks the document's
paragraph stream, tracks heading levels, and emits one section per
heading + leading-content pair.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Protocol

from bs4 import BeautifulSoup
from pypdf import PdfReader

from flycanon.core.services.ingestion.errors import CorruptSource
from flycanon.interfaces.enums import SourceKind


@dataclass(slots=True)
class Section:
    """A heading + body pair recovered by the loader.

    ``path`` is the breadcrumb of headings leading to this section
    (e.g. ``["Scope", "In scope"]``). ``order`` keeps the loader's
    emission order so chunks remain stable across reruns.
    """

    path: list[str]
    body: str
    order: int = 0
    page: int | None = None


@dataclass(slots=True)
class LoadedDocument:
    """Normalised view of a source the chunker can consume."""

    sections: list[Section] = field(default_factory=list)
    page_count: int | None = None
    language: str | None = None
    title: str | None = None
    raw_text: str | None = None


class SourceLoader(Protocol):
    """Bytes (or pre-decoded text) -> :class:`LoadedDocument`."""

    kind: SourceKind

    def load(self, content: bytes | str, *, filename: str | None = None) -> LoadedDocument: ...


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


class DocxLoader:
    """python-docx-backed DOCX loader.

    Walks the paragraph stream, tracks ``Heading1`` ... ``Heading9``
    styles, and emits one section per heading. Body paragraphs
    (including ``ListParagraph``) are coalesced under the most recent
    heading. The leading "no heading" body (intro paragraphs that
    appear before the first heading) becomes the first section with
    an empty ``path``.
    """

    kind = SourceKind.docx

    def load(self, content: bytes | str, *, filename: str | None = None) -> LoadedDocument:
        try:
            from docx import Document
        except ImportError as exc:  # pragma: no cover - dependency wired in pyproject
            raise CorruptSource("docx", f"python-docx is not installed: {exc}") from exc

        try:
            if isinstance(content, str):
                content = content.encode("utf-8")
            document = Document(io.BytesIO(content))
        except Exception as exc:  # python-docx raises a wide range of exceptions
            raise CorruptSource("docx", str(exc)) from exc

        sections: list[Section] = []
        heading_stack: list[str] = []
        current_path: list[str] = []
        current_body: list[str] = []
        order = 0

        def _flush() -> None:
            nonlocal order
            body = "\n".join(line for line in current_body if line.strip())
            if not body:
                return
            sections.append(Section(path=list(current_path), body=body, order=order))
            order += 1

        title: str | None = None
        for para in document.paragraphs:
            style = (para.style.name if para.style else "") or ""
            text = (para.text or "").strip()
            if not text:
                continue
            heading_match = re.match(r"Heading\s*(\d+)", style)
            if style == "Title":
                title = title or text
                continue
            if heading_match:
                # Close the previous block before re-anchoring.
                _flush()
                current_body = []
                level = int(heading_match.group(1))
                # Truncate the heading stack so the new heading slots
                # in at its level.
                while len(heading_stack) >= level:
                    heading_stack.pop()
                heading_stack.append(text)
                current_path = list(heading_stack)
                continue
            current_body.append(text)
        _flush()

        if not sections:
            return LoadedDocument(title=title)

        return LoadedDocument(sections=sections, title=title)


class PdfLoader:
    """pypdf-backed loader for born-digital PDFs.

    Image-only PDFs produce empty text per page; the ingestion
    pipeline raises :class:`EmptySource` upstream when no chunk has
    extractable text. flycanon's intake path assumes the source is
    semantically text-bearing -- layout-aware OCR / bbox refinement
    is a sibling-service concern and is not run here.
    """

    kind = SourceKind.pdf

    def load(self, content: bytes | str, *, filename: str | None = None) -> LoadedDocument:
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            reader = PdfReader(io.BytesIO(content))
        except Exception as exc:
            raise CorruptSource("pdf", str(exc)) from exc

        if reader.is_encrypted:
            raise CorruptSource("pdf", "encrypted PDFs are not supported")

        sections: list[Section] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
            if not text:
                continue
            sections.append(
                Section(path=[f"Page {page_idx}"], body=text, order=page_idx - 1, page=page_idx)
            )
        return LoadedDocument(sections=sections, page_count=len(reader.pages))


class HtmlLoader:
    """BeautifulSoup-backed HTML loader.

    Heading tags (``h1`` ... ``h6``) drive section boundaries; their
    text becomes the section path. Paragraph + list-item nodes are
    coalesced under the most recent heading.
    """

    kind = SourceKind.html

    def load(self, content: bytes | str, *, filename: str | None = None) -> LoadedDocument:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        try:
            soup = BeautifulSoup(content, "lxml")
        except Exception:
            soup = BeautifulSoup(content, "html.parser")

        title = (soup.title.string.strip() if soup.title and soup.title.string else None)

        sections: list[Section] = []
        heading_stack: list[str] = []
        current_path: list[str] = []
        current_body: list[str] = []
        order = 0

        def _flush() -> None:
            nonlocal order
            body = "\n".join(line for line in current_body if line.strip())
            if not body:
                return
            sections.append(Section(path=list(current_path), body=body, order=order))
            order += 1

        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
            text = tag.get_text(" ", strip=True)
            if not text:
                continue
            if tag.name and tag.name.startswith("h") and tag.name[1:].isdigit():
                _flush()
                current_body = []
                level = int(tag.name[1:])
                while len(heading_stack) >= level:
                    heading_stack.pop()
                heading_stack.append(text)
                current_path = list(heading_stack)
                continue
            current_body.append(text)
        _flush()

        return LoadedDocument(sections=sections, title=title)


class MarkdownLoader:
    """Plain-text walk over Markdown.

    Headings are detected by line prefix (``# `` ... ``###### ``); the
    leading body becomes a section with an empty ``path``.
    """

    kind = SourceKind.markdown

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

    def load(self, content: bytes | str, *, filename: str | None = None) -> LoadedDocument:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        sections: list[Section] = []
        heading_stack: list[str] = []
        current_path: list[str] = []
        current_body: list[str] = []
        order = 0

        def _flush() -> None:
            nonlocal order
            body = "\n".join(line for line in current_body if line.strip())
            if not body:
                return
            sections.append(Section(path=list(current_path), body=body, order=order))
            order += 1

        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            match = self._HEADING_RE.match(line)
            if match:
                _flush()
                current_body = []
                level = len(match.group(1))
                title = match.group(2).strip()
                while len(heading_stack) >= level:
                    heading_stack.pop()
                heading_stack.append(title)
                current_path = list(heading_stack)
                continue
            current_body.append(line)
        _flush()

        return LoadedDocument(sections=sections)


class TextLoader:
    """Plain-text loader -- the whole content lands as one section."""

    kind = SourceKind.text

    def load(self, content: bytes | str, *, filename: str | None = None) -> LoadedDocument:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        body = content.strip()
        if not body:
            return LoadedDocument(raw_text="")
        return LoadedDocument(sections=[Section(path=[], body=body, order=0)], raw_text=body)


class LoaderRegistry:
    """Lookup table from :class:`SourceKind` to loader."""

    def __init__(self) -> None:
        self._loaders: dict[SourceKind, SourceLoader] = {}

    def register(self, loader: SourceLoader) -> None:
        self._loaders[loader.kind] = loader

    def get(self, kind: SourceKind) -> SourceLoader | None:
        return self._loaders.get(kind)

    def __contains__(self, kind: SourceKind) -> bool:
        return kind in self._loaders


def default_registry() -> LoaderRegistry:
    """Build the registry with every shipped loader pre-installed."""
    registry = LoaderRegistry()
    registry.register(DocxLoader())
    registry.register(PdfLoader())
    registry.register(HtmlLoader())
    registry.register(MarkdownLoader())
    registry.register(TextLoader())
    return registry
