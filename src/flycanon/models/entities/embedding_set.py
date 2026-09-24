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

"""``canon_embedding_sets`` -- one coherent embedding space per workspace.

An *embedding set* is every vector in ``canon_chunk_vectors`` that one
(provider, model, dimensions, endpoint) configuration produced for one
workspace. It is the unit a deployment re-embeds into and the unit search
reads from: ``canon_workspaces.active_embedding_set_id`` names the one that
answers queries, and flipping that column is the whole of an embedder change.

Why a set id and not just ``(model, dimensions)``. Routing on the pair cannot
express a re-embed with the SAME model at the SAME width, which is exactly
what a deployment needs after a batch of vectors was written badly (a provider
outage that produced zero vectors, a provider that re-hosted a model id under
the same name). A set id can, and an atomic switch needs one scalar to update
rather than a pair of strings compared by two services.

``config_fingerprint`` is deliberately NOT unique per workspace, for the same
reason: re-embedding onto the identical configuration is a supported, and
sometimes the only correct, operation.

Scoped by ``(tenant_id, workspace_id)`` under migration 0013's standard RLS
policy family, installed by 0017 alongside the table.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base

#: ``building`` -- rows are being written; invisible to search.
#: ``ready``    -- every chunk embedded and the ANN index built.
#: ``active``   -- the workspace points at it; it answers queries.
#: ``retired``  -- it was active and was superseded; rows and index kept so
#:                 ``flycanon reindex --rollback`` is one UPDATE away.
#: ``failed``   -- the run gave up; the rows are incomplete and must not serve.
SET_STATUSES: Final[tuple[str, ...]] = ("building", "ready", "active", "retired", "failed")


def new_embedding_set_id() -> str:
    """Mint a set id. Prefixed so it is recognisable in a log line or a CLI flag."""
    return f"es-{uuid.uuid4().hex}"


def config_fingerprint(*, provider: str, model: str, dimensions: int, endpoint: str, api_version: str) -> str:
    """Hash the configuration that produced a set.

    Two sets with the same fingerprint are comparable embedding spaces; two
    with different fingerprints are not, even at the same width. The endpoint
    host is in the hash because the same model name on two Azure resources is
    two deployments that may not be the same model at all -- on Azure the
    ``model=`` argument is a DEPLOYMENT name, and nothing stops two resources
    from pointing the same name at different weights.
    """
    material = "\x1f".join(
        [provider.strip().lower(), model.strip(), str(int(dimensions)), endpoint.strip(), api_version.strip()]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


#: pgvector will not build an HNSW index on a ``vector`` column wider than
#: this (``column cannot have more than 2000 dimensions for hnsw index``,
#: measured on pgvector 0.8.6). It WILL build one on ``halfvec`` up to 4000.
HNSW_MAX_VECTOR_DIMENSIONS = 2000


def ann_cast(dimensions: int) -> tuple[str, str]:
    """The type an ANN index and its matching ``ORDER BY`` cast to at this width.

    This is not a micro-optimisation, it is the difference between an indexed
    search and a sequential scan on the exact configuration this release
    exists to support: ``text-embedding-3-large`` is 3072-wide natively, and
    pgvector refuses an HNSW on a ``vector`` column above 2000 dimensions.
    Half precision is the documented answer -- the values are stored at full
    precision in the column and only the INDEX is half, so the cost is a small
    loss of ranking precision in the candidate set, not in the data.

    The index expression and the query's ``ORDER BY`` have to agree character
    for character, so both go through here.
    """
    if dimensions > HNSW_MAX_VECTOR_DIMENSIONS:
        return "halfvec", "halfvec_cosine_ops"
    return "vector", "vector_cosine_ops"


def index_name_for(set_id: str, *, table: str = "canon_chunk_vectors") -> str:
    """The partial HNSW index that serves one set.

    One index per SET rather than per width: a per-width index would force a
    dual-set window at one width into a filtered ANN scan across both sets,
    which is a recall hazard precisely during a switch, when correctness
    matters most.
    """
    return f"{table}_hnsw_{set_id.replace('-', '_')}"


class EmbeddingSetRow(Base):
    __tablename__ = "canon_embedding_sets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_embedding_set_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: Embedder identity, split so a query can group by either half.
    #: ``model`` is the DEPLOYMENT name on Azure, not the model name.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Progress + forensics. ``chunk_count`` is what the run expected to
    #: write, ``vector_count`` what it has written -- equal on a ready set,
    #: and their inequality is what tells an operator a run stopped early.
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("dimensions BETWEEN 64 AND 4096", name="ck_canon_embedding_sets_dimensions"),
        Index("ix_canon_embedding_sets_tenant_workspace", "tenant_id", "workspace_id"),
    )

    @property
    def embedding_model(self) -> str:
        """The ``<provider>:<model>`` identifier this set was built with."""
        return f"{self.provider}:{self.model}"
