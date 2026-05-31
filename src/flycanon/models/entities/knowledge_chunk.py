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

"""``canon_chunks`` -- retrieval-grade fragments of a source.

A chunk carries:

* the verbatim text fragment (``content``),
* an optional pre-computed embedding (``embedding``) -- nullable so
  re-embedding with a different provider is a no-op for the schema,
* a serialised hint for the loader's positional bookkeeping
  (``positional``) so callers can reconstruct a citation context.

The BM25 projection lives in Postgres -- the generated ``tsv`` column on
``canon_chunks`` queried by :class:`PostgresCorpus`; the row here is the
system-of-record for the chunk's content + provenance.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class KnowledgeChunkRow(Base):
    __tablename__ = "canon_chunks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("canon_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Position within the source: 0-based.
    index_in_source: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Verbatim content + raw character offsets for citation rendering.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Heading path (e.g. ``Scope > In scope``) recovered by the loader.",
    )

    # Optional dense embedding. Kept JSON for backend portability; the
    # pgvector adapter mirrors this column into a typed vector when
    # ``FLYCANON_VECTOR_STORE=pgvector`` is set.
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_canon_chunks_source_idx", "source_id", "index_in_source"),
        Index("ix_canon_chunks_tenant_workspace", "tenant_id", "workspace_id"),
        Index(
            "ix_canon_chunks_tenant_workspace_model",
            "tenant_id",
            "workspace_id",
            "embedding_model",
        ),
        Index(
            "ix_canon_chunks_scope_source",
            "tenant_id",
            "workspace_id",
            "source_id",
            "index_in_source",
        ),
    )
