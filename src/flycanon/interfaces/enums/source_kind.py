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

    # --- Cross-service handoffs --------------------------------------
    # Used by flyradar's ``POST /api/v1/agent/canon/handoff`` to label
    # the resulting Source as originating from a completed discovery.
    # The content rides on ``content_base64`` (the discovery summary
    # serialised as JSON); the originating job is preserved on
    # ``metadata.extra.flyradar_job_id`` for provenance trails.
    discovery = "discovery"
