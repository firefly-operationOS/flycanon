# Copyright 2026 Firefly Software Solutions Inc
"""``SourceKind`` -- the canonical wire identifier for a source format.

Each value maps to one entry in the routing matrix that the
:class:`BinaryNormalizer` uses to pick a downstream adapter, and to
one entry in the :class:`LoaderRegistry` that turns the canonical
bytes into a :class:`LoadedDocument`.

The enum is a *public* type -- it travels on the wire as the
``kind`` field of every ``SourceRecord`` -- so renaming a member is a
breaking change.
"""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    # --- Office / structured documents -------------------------------
    docx = "docx"
    xlsx = "xlsx"
    pptx = "pptx"
    pdf = "pdf"
    rtf = "rtf"
    odt = "odt"
    ods = "ods"
    odp = "odp"

    # --- Web / text formats ------------------------------------------
    html = "html"
    markdown = "markdown"
    text = "text"
    csv = "csv"
    tsv = "tsv"
    json_ = "json"
    xml = "xml"
    epub = "epub"

    # --- Raster / vector images (OCR'd at intake) --------------------
    image = "image"

    # --- Aggregates --------------------------------------------------
    archive = "archive"
    email = "email"

    # --- Transcripts (audio/video pre-extracted) ---------------------
    transcript = "transcript"

    # --- Operator hints ----------------------------------------------
    url = "url"
    unknown = "unknown"
