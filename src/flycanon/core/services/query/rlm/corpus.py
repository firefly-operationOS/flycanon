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

"""The corpus the RLM engine inspects as a variable.

RLM's premise is "context as an object in code": rather than pasting documents
into the prompt, the whole corpus is handed to the orchestrator as the variable
``docs``, which it inspects with Python. :class:`CanonDocStore` is that variable
-- a synchronous, in-memory dict-like over the in-scope workspace sources.
``docs.keys()`` lists readable document keys (derived from each source's
filename), ``docs[key]`` returns the full document text, ``docs.pages(key)``
returns its page list for precise citation, and every access is recorded so the
caller can map what the model touched back to source rows for citations.

The store is deliberately synchronous: the engine runs it inside
``asyncio.to_thread``. The asynchronous work -- listing sources, fetching
originals from the :class:`ObjectStore`, and extracting page text -- happens up
front in :class:`CanonCorpusBuilder`, which materialises a :class:`CanonDocStore`
the engine then drives without further I/O.

``CanonDocStore`` structurally satisfies the ``DocCorpus`` protocol defined in
``session.py`` (it is not imported here -- the engine duck-types ``docs``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import fitz  # PyMuPDF

from flycanon.core.services.ingestion.loaders import LoaderRegistry, default_registry
from flycanon.core.services.storage.object_store import ObjectStore
from flycanon.interfaces.enums import SourceKind
from flycanon.models.entities.source import SourceRow
from flycanon.models.repositories.knowledge_repository import KnowledgeRepository
from flycanon.models.repositories.source_repository import SourceRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceMeta:
    """The source pointer kept alongside each document for citations.

    Mirrors the citation-relevant fields the RAG ``Hit`` carries
    (``source_id`` / ``source_filename`` / ``source_title`` /
    ``source_kind``) so the answer service can hydrate citations the same
    way regardless of which retrieval engine produced them.
    """

    source_id: str
    filename: str | None
    title: str | None
    kind: str


def extract_pdf_pages(data: bytes) -> list[str]:
    """Per-page text of a PDF, via PyMuPDF on the original bytes.

    Mirrors the experiment's ``corpus.py`` page extraction. Factored into a
    module-level function so tests can monkeypatch it without a real PDF.
    """
    with fitz.open(stream=data, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


class CanonDocStore:
    """In-memory, synchronous whole-document corpus for the RLM REPL.

    Holds ``{readable_key: pages}`` plus a parallel ``{readable_key:
    SourceMeta}`` map for citation mapping. ``accessed`` records, in order,
    the keys the model touched via ``__getitem__`` / ``pages`` -- exactly
    like the experiment's ``DocStore`` -- so the caller can credit the
    sources the model actually read.
    """

    def __init__(
        self,
        pages: dict[str, list[str]],
        sources: dict[str, SourceMeta],
    ) -> None:
        self._pages = pages
        self._sources = sources
        self.accessed: list[str] = []  # ordered, deduped: keys the model touched

    # -- dict-like surface the model uses from REPL code --
    def keys(self):
        return list(self._pages.keys())

    def __iter__(self):
        return iter(self._pages)

    def __contains__(self, key: object) -> bool:
        return key in self._pages

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, key: str) -> str:
        """Full document text (pages joined). Records the access."""
        self._record(key)
        return "\n".join(self._pages[key])

    def pages(self, key: str) -> list[str]:
        """Per-page text for ``key`` (for citing specific pages). Records the access."""
        self._record(key)
        return list(self._pages[key])

    def npages(self, key: str) -> int:
        return len(self._pages[key])

    # -- citation mapping --
    def resolve(self, key: str) -> SourceMeta | None:
        """Source pointer behind ``key`` (for citations), or ``None``."""
        return self._sources.get(key)

    # -- internals --
    def _record(self, key: str) -> None:
        if key in self._pages and key not in self.accessed:
            self.accessed.append(key)


class CanonCorpusBuilder:
    """Builds a :class:`CanonDocStore` from the in-scope workspace sources.

    Lists the workspace's sources (applying the ``AnswerRequest`` filters with
    the same AND / no-op semantics as the RAG retrieval path), fetches each
    original from the :class:`ObjectStore`, and extracts page-structured text:
    PyMuPDF per page for PDFs; the matching loader otherwise (one section per
    page). Sources without an ``object_store_key`` have no stored original to
    read and are skipped with a log line.
    """

    def __init__(
        self,
        *,
        source_repository: SourceRepository,
        knowledge_repository: KnowledgeRepository,
        object_store: ObjectStore,
        registry: LoaderRegistry | None = None,
    ) -> None:
        self._sources = source_repository
        self._knowledge = knowledge_repository
        self._object_store = object_store
        self._registry = registry or default_registry()

    async def build(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        filters: Filters | None = None,
    ) -> CanonDocStore:
        rows = await self._list_in_scope(tenant_id, workspace_id, filters)

        pages: dict[str, list[str]] = {}
        sources: dict[str, SourceMeta] = {}
        used_keys: set[str] = set()
        for row in rows:
            if not row.object_store_key:
                logger.info(
                    "rlm corpus skipping source without stored original source_id=%s filename=%s",
                    row.id,
                    row.filename,
                )
                continue
            data = await self._object_store.get(row.object_store_key)
            page_list = self._pages_for(row, data)
            if not page_list:
                logger.info(
                    "rlm corpus skipping source with no extractable text source_id=%s filename=%s",
                    row.id,
                    row.filename,
                )
                continue
            key = self._readable_key(row, used_keys)
            used_keys.add(key)
            pages[key] = page_list
            sources[key] = _source_meta(row)
        return CanonDocStore(pages, sources)

    async def _list_in_scope(
        self,
        tenant_id: str,
        workspace_id: str,
        filters: Filters | None,
    ) -> list[SourceRow]:
        """Workspace sources after applying the filters (AND, empty = no-op).

        ``statuses`` is pushed into the repository query (it is a first-class
        ``SourceRow`` column); the remaining dimensions live on
        ``metadata_json`` (``domain`` / ``jurisdiction`` / ``tags``, mirroring
        the RAG hydration) or are the row id, so they are matched in-process.

        ``knowledge_item_ids`` is the one dimension that needs a repo
        round-trip: it is resolved to the set of ``source_id``\\ s that those
        items' **current-version** citations point at -- the whole-document,
        source-keyed analogue of the retrieval path's chunk-level
        ``knowledge_item_ids`` filter -- and AND-composed with the rest. An
        empty resolution (the items cite nothing) restricts the corpus to
        nothing, matching the RAG path where no hit would survive the filter.
        """
        f = filters or Filters()
        knowledge_source_ids = (
            await self._knowledge.resolve_source_ids_for_items(
                f.knowledge_item_ids,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if f.knowledge_item_ids
            else None
        )
        rows: list[SourceRow] = []
        offset = 0
        page = 200
        while True:
            batch, total = await self._sources.list_sources(
                statuses=list(f.statuses) if f.statuses else None,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                limit=page,
                offset=offset,
            )
            rows.extend(batch)
            offset += len(batch)
            if not batch or offset >= total:
                break
        return [row for row in rows if _matches(row, f, knowledge_source_ids)]

    def _pages_for(self, row: SourceRow, data: bytes) -> list[str]:
        """Page-structured text for a source's original bytes.

        PDFs go through PyMuPDF per page (mirrors the experiment). Everything
        else goes through the matching loader: each loader section becomes a
        page, falling back to the loaded ``raw_text`` as a single page.
        """
        kind = _source_kind(row.kind)
        if kind is SourceKind.pdf:
            return [text for text in extract_pdf_pages(data) if text and text.strip()]

        loader = self._registry.get(kind)
        if loader is None:
            return []
        document = loader.load(data, filename=row.filename)
        pages = [section.body for section in document.sections if section.body and section.body.strip()]
        if pages:
            return pages
        if document.raw_text and document.raw_text.strip():
            return [document.raw_text]
        return []

    def _readable_key(self, row: SourceRow, used: set[str]) -> str:
        """A routing-friendly key from the filename stem (fallbacks: title, id).

        De-duplicated across sources by suffixing ``#2``, ``#3`` ... so two
        files sharing a stem stay distinct keys.
        """
        base = _key_base(row)
        if base not in used:
            return base
        n = 2
        while f"{base}#{n}" in used:
            n += 1
        return f"{base}#{n}"


@dataclass(slots=True)
class Filters:
    """Corpus-scoping filters -- the ``AnswerRequest`` filter fields.

    Compose with AND; empty / ``None`` is a no-op, mirroring the RAG
    ``_Filters`` semantics. The values are plain strings (the caller passes
    enum ``.value``\\ s, as the RAG search service does).
    """

    source_ids: Sequence[str] | None = None
    knowledge_item_ids: Sequence[str] | None = None
    domains: Sequence[str] | None = None
    jurisdictions: Sequence[str] | None = None
    tags: Sequence[str] | None = None
    statuses: Sequence[str] | None = None


def _matches(row: SourceRow, f: Filters, knowledge_source_ids: set[str] | None) -> bool:
    """Whether a source row passes the in-process filter dimensions.

    ``source_ids`` matches the row id; ``domains`` / ``jurisdictions`` / ``tags``
    read the source's ``metadata_json`` hints (the same keys the RAG hydration
    falls back to when no knowledge linkage exists). ``knowledge_item_ids`` is
    pre-resolved by the caller to ``knowledge_source_ids`` -- the set of
    ``source_id``\\ s those items' current-version citations point at -- which
    the row id must be in (``None`` means the filter is absent / a no-op; an
    empty set means the items cite nothing, so nothing matches).
    """
    if f.source_ids and row.id not in set(f.source_ids):
        return False
    if knowledge_source_ids is not None and row.id not in knowledge_source_ids:
        return False
    meta = row.metadata_json or {}
    if f.domains and str(meta.get("domain")) not in set(f.domains):
        return False
    if f.jurisdictions and str(meta.get("jurisdiction")) not in set(f.jurisdictions):
        return False
    if f.tags:
        row_tags = {str(t) for t in (meta.get("tags") or [])}
        if not (set(f.tags) & row_tags):
            return False
    return True


def _source_kind(kind: str) -> SourceKind:
    """Map a row's ``kind`` string to :class:`SourceKind`, defaulting to text."""
    try:
        return SourceKind(kind)
    except ValueError:
        return SourceKind.text


def _key_base(row: SourceRow) -> str:
    """Filename stem, falling back to title then source id."""
    if row.filename:
        stem = row.filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
        if stem:
            return stem
    meta = row.metadata_json or {}
    title = meta.get("title") or (meta.get("extracted") or {}).get("title")
    if title and str(title).strip():
        return str(title).strip()
    return row.id


def _source_meta(row: SourceRow) -> SourceMeta:
    meta = row.metadata_json or {}
    title = meta.get("title") or (meta.get("extracted") or {}).get("title")
    return SourceMeta(
        source_id=row.id,
        filename=row.filename,
        title=str(title) if title else None,
        kind=row.kind,
    )
