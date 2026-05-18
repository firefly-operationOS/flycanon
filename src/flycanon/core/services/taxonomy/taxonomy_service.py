# Copyright 2026 Firefly Software Solutions Inc
"""Taxonomy registry -- one row per node in the domain / jurisdiction tree.

The service seeds a root node per :class:`Domain` value at boot when
the table is empty; callers attach children at runtime via
:meth:`add_node`. Reads are flat and ordered by ``depth`` so the
breadth-first view is index-only.
"""

from __future__ import annotations

import logging

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.interfaces.dtos.taxonomy import CreateTaxonomyNodeRequest
from flycanon.interfaces.enums import Domain
from flycanon.models.entities.taxonomy_node import TaxonomyNodeRow
from flycanon.models.repositories.taxonomy_repository import TaxonomyRepository

logger = logging.getLogger(__name__)


class TaxonomyNotFound(Exception):
    code = "taxonomy_node_not_found"
    http_status = 404

    def __init__(self, node_id: str) -> None:
        super().__init__(f"taxonomy node {node_id!r} not found")
        self.node_id = node_id


class TaxonomyService:
    def __init__(
        self,
        *,
        repository: TaxonomyRepository,
        audit: AuditService,
        settings: CanonSettings,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._settings = settings

    async def list_all(self) -> list[TaxonomyNodeRow]:
        return await self._repository.list_all()

    async def add_node(
        self,
        request: CreateTaxonomyNodeRequest,
        *,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> TaxonomyNodeRow:
        parent_depth = 0
        if request.parent_id:
            parent = await self._repository.get(request.parent_id)
            if parent is None:
                raise TaxonomyNotFound(request.parent_id)
            parent_depth = parent.depth + 1
        node = TaxonomyNodeRow(
            parent_id=request.parent_id,
            slug=request.slug,
            label=request.label,
            domain=request.domain.value,
            description=request.description,
            depth=parent_depth,
        )
        stored = await self._repository.add(node)
        await self._audit.record(
            event_type="taxonomy.node_added",
            subject_kind="taxonomy",
            subject_id=stored.id,
            actor=actor,
            correlation_id=correlation_id,
            payload={
                "parent_id": stored.parent_id,
                "slug": stored.slug,
                "domain": stored.domain,
            },
        )
        return stored

    async def ensure_default_seed(self) -> int:
        """Ensure one root node exists per :class:`Domain` value.

        Returns the number of root nodes inserted. Idempotent --
        safe to call on every boot.
        """
        existing = {row.slug for row in await self._repository.list_all() if row.parent_id is None}
        inserted = 0
        for domain in Domain:
            if domain.value in existing:
                continue
            node = TaxonomyNodeRow(
                parent_id=None,
                slug=domain.value,
                label=_pretty_label(domain.value),
                domain=domain.value,
                depth=0,
                description=f"Root node for the {domain.value} domain.",
            )
            await self._repository.add(node)
            inserted += 1
        if inserted:
            logger.info("taxonomy seed inserted %d root node(s)", inserted)
        return inserted


def _pretty_label(slug: str) -> str:
    return slug.replace("_", " ").title()
