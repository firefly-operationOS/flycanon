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

"""Embedding sets -- which embedding space a read or a write belongs to.

Two things live here:

* :class:`EmbeddingSetBinding` and :func:`bind_embedding_set`, a ContextVar
  that carries the set a piece of work is operating in. The framework's
  vector-store surface is ``upsert(documents, namespace)`` /
  ``search(vector, top_k, namespace)`` -- there is nowhere on it to pass a
  set -- and threading one through :class:`TenantScopedVectorStore` would
  fork the framework. A ContextVar is how the request scope already travels
  (:mod:`flycanon.web.conventions.context`), so the set travels the same way,
  and :class:`RlsPgVectorVectorStore` reads it in the same breath as the
  namespace.
* :class:`EmbeddingSetService`, which resolves the set for a workspace,
  creates new ones, and performs the atomic switch and its inverse.

The rule the whole module exists to enforce: **a query is embedded with the
model that produced the corpus it is searching**, not with whatever
``FLYCANON_EMBEDDING_MODEL`` happens to say in this process. One shared
flycanon legitimately serves workspaces sitting on different sets.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from flycanon.config import CanonSettings
from flycanon.models.entities.embedding_set import (
    EmbeddingSetRow,
    config_fingerprint,
    index_name_for,
    new_embedding_set_id,
)
from flycanon.models.repositories.embedding_set_repository import EmbeddingSetRepository

logger = logging.getLogger(__name__)


class EmbeddingSetError(Exception):
    """Raised when a set cannot be resolved, created or switched."""


@dataclass(frozen=True, slots=True)
class EmbeddingSetBinding:
    """The embedding space one unit of work reads from or writes into."""

    set_id: str
    provider: str
    model: str
    dimensions: int

    @property
    def embedding_model(self) -> str:
        """The ``<provider>:<model>`` identifier stamped on every vector row."""
        return f"{self.provider}:{self.model}"

    @classmethod
    def of(cls, row: EmbeddingSetRow) -> EmbeddingSetBinding:
        return cls(
            set_id=row.id,
            provider=row.provider,
            model=row.model,
            dimensions=int(row.dimensions),
        )


_current_set: ContextVar[EmbeddingSetBinding | None] = ContextVar("flycanon_embedding_set", default=None)


def current_embedding_set() -> EmbeddingSetBinding | None:
    """The set bound to this task, or ``None`` outside a bound block."""
    return _current_set.get()


@contextmanager
def bind_embedding_set(binding: EmbeddingSetBinding) -> Iterator[EmbeddingSetBinding]:
    """Bind ``binding`` for the duration of the block.

    ContextVars follow ``asyncio`` tasks and ``asyncio.to_thread`` calls, so a
    retrieval that fans out across BM25 and ANN keeps one set for both halves,
    and a reindex batch keeps the TARGET set while the serving path elsewhere
    in the process keeps the active one.
    """
    token = _current_set.set(binding)
    try:
        yield binding
    finally:
        _current_set.reset(token)


def split_embedding_model(embedding_model: str) -> tuple[str, str]:
    """``"azure:text-embedding-3-large"`` -> ``("azure", "text-embedding-3-large")``.

    On Azure the second half is the DEPLOYMENT name, not the model name.
    """
    provider, separator, model = embedding_model.partition(":")
    if not separator or not provider.strip() or not model.strip():
        raise EmbeddingSetError(
            f"an embedding model must be ``<provider>:<model>`` (got {embedding_model!r}); "
            "on Azure the second half is the deployment name, e.g. azure:my-emb-3-large-deploy"
        )
    return provider.strip().lower(), model.strip()


class EmbeddingSetService:
    """Resolve, create, activate and retire a workspace's embedding sets."""

    def __init__(self, *, repository: EmbeddingSetRepository, settings: CanonSettings) -> None:
        self._repository = repository
        self._settings = settings

    # ------------------------------------------------------------------
    # The process default -- what a workspace with no set of its own gets
    # ------------------------------------------------------------------

    @property
    def default_provider_model(self) -> tuple[str, str]:
        return split_embedding_model(self._settings.embedding_model)

    def _fingerprint(self, *, provider: str, model: str, dimensions: int) -> str:
        azure = provider.startswith("azure")
        return config_fingerprint(
            provider=provider,
            model=model,
            dimensions=dimensions,
            endpoint=self._settings.azure_openai_endpoint if azure else "",
            api_version=self._settings.azure_openai_api_version if azure else "",
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def active_binding(self, *, tenant_id: str, workspace_id: str) -> EmbeddingSetBinding | None:
        """The set that answers this workspace's searches, or ``None``.

        ``None`` means the workspace has never been indexed under a set: it is
        the signal to :meth:`ensure_active` to mint one from the process
        default, and the signal to the read path that there is nothing to read.
        """
        set_id = await self._repository.active_set_id(tenant_id=tenant_id, workspace_id=workspace_id)
        if not set_id:
            return None
        row = await self._repository.get(tenant_id=tenant_id, workspace_id=workspace_id, set_id=set_id)
        if row is None:
            raise EmbeddingSetError(
                f"workspace {workspace_id} points at embedding set {set_id}, which does not exist. "
                "Repair with `flycanon reindex --list` and `flycanon reindex --activate <set-id>`."
            )
        return EmbeddingSetBinding.of(row)

    async def list_for(self, *, tenant_id: str, workspace_id: str) -> list[EmbeddingSetRow]:
        return await self._repository.list_for_workspace(tenant_id=tenant_id, workspace_id=workspace_id)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def ensure_active(self, *, tenant_id: str, workspace_id: str) -> EmbeddingSetBinding:
        """Return the workspace's active set, minting the first one if needed.

        The first ingest into a workspace creates a set from the process
        default and points the workspace at it. That is what makes this whole
        mechanism invisible to a single-embedder deployment: it never chooses
        a set, and the one it gets is labelled with the configuration that
        produced it, which is the point.
        """
        binding = await self.active_binding(tenant_id=tenant_id, workspace_id=workspace_id)
        if binding is not None:
            return binding
        provider, model = self.default_provider_model
        row = await self.create(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider=provider,
            model=model,
            dimensions=self._settings.embedding_dimensions,
            status="active",
            note="minted from the process default on first index",
        )
        await self._repository.point_workspace_at(
            tenant_id=tenant_id, workspace_id=workspace_id, set_id=row.id
        )
        logger.info(
            "embedding set %s created for workspace %s (%s:%s @%d) and activated",
            row.id,
            workspace_id,
            provider,
            model,
            row.dimensions,
        )
        return EmbeddingSetBinding.of(row)

    async def create(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        provider: str,
        model: str,
        dimensions: int,
        status: str = "building",
        chunk_count: int = 0,
        created_by: str | None = None,
        note: str | None = None,
    ) -> EmbeddingSetRow:
        set_id = new_embedding_set_id()
        row = EmbeddingSetRow(
            id=set_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider=provider,
            model=model,
            dimensions=int(dimensions),
            status=status,
            config_fingerprint=self._fingerprint(provider=provider, model=model, dimensions=dimensions),
            chunk_count=chunk_count,
            vector_count=0,
            index_name=index_name_for(set_id),
            created_by=created_by,
            note=note,
        )
        if status == "active":
            from datetime import UTC, datetime

            row.activated_at = datetime.now(UTC)
        return await self._repository.insert(row)

    async def mark(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        set_id: str,
        status: str,
        vector_count: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        await self._repository.set_status(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            set_id=set_id,
            status=status,
            vector_count=vector_count,
            chunk_count=chunk_count,
        )

    async def activate(self, *, tenant_id: str, workspace_id: str, set_id: str) -> str | None:
        """Point the workspace at ``set_id``; retire whatever it pointed at.

        Returns the id of the set that was retired, so the caller can tell an
        operator what ``--rollback`` would go back to. Rows and index of the
        retired set are kept: the rollback window is a policy the operator
        chooses with ``--drop-set``, not a timeout the code imposes.
        """
        target = await self._repository.get(tenant_id=tenant_id, workspace_id=workspace_id, set_id=set_id)
        if target is None:
            raise EmbeddingSetError(f"embedding set {set_id} does not exist in workspace {workspace_id}")
        if target.status not in ("ready", "active", "retired"):
            raise EmbeddingSetError(
                f"embedding set {set_id} is {target.status!r}; only a ready (or previously active) set "
                "can serve searches. A set that is still building holds an incomplete corpus."
            )
        previous = await self._repository.active_set_id(tenant_id=tenant_id, workspace_id=workspace_id)
        await self._repository.point_workspace_at(
            tenant_id=tenant_id, workspace_id=workspace_id, set_id=set_id
        )
        await self._repository.set_status(
            tenant_id=tenant_id, workspace_id=workspace_id, set_id=set_id, status="active"
        )
        if previous and previous != set_id:
            await self._repository.set_status(
                tenant_id=tenant_id, workspace_id=workspace_id, set_id=previous, status="retired"
            )
        logger.info(
            "workspace %s now serves embedding set %s (was %s)", workspace_id, set_id, previous or "none"
        )
        return previous if previous != set_id else None

    async def rollback(self, *, tenant_id: str, workspace_id: str) -> str:
        """Go back to the set that was active before the last switch."""
        current = await self._repository.active_set_id(tenant_id=tenant_id, workspace_id=workspace_id)
        if not current:
            raise EmbeddingSetError(
                f"workspace {workspace_id} has no active embedding set; there is nothing to roll back from"
            )
        previous = await self._repository.previous_active(
            tenant_id=tenant_id, workspace_id=workspace_id, exclude_set_id=current
        )
        if previous is None:
            raise EmbeddingSetError(
                f"workspace {workspace_id} has no retired embedding set to roll back to. "
                "A set that was dropped (`flycanon reindex --drop-set`) cannot be rolled back to; "
                "it has to be rebuilt."
            )
        await self.activate(tenant_id=tenant_id, workspace_id=workspace_id, set_id=previous.id)
        return previous.id
