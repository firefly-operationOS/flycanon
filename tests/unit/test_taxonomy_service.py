# Copyright 2026 Firefly Software Solutions Inc
"""Taxonomy service seed + tree-extension behaviour."""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.audit import AuditService
from flycanon.core.services.taxonomy import TaxonomyService
from flycanon.interfaces.dtos.taxonomy import CreateTaxonomyNodeRequest
from flycanon.interfaces.enums import Domain


@pytest.mark.asyncio
async def test_seed_inserts_one_root_per_domain(repositories):
    audit = AuditService(
        repository=repositories["audit"],
        event_publisher=None,
        settings=CanonSettings(),
    )
    taxonomy = TaxonomyService(
        repository=repositories["taxonomy"],
        audit=audit,
        settings=CanonSettings(),
    )
    inserted = await taxonomy.ensure_default_seed()
    assert inserted == len(Domain)
    # Calling again is a no-op.
    again = await taxonomy.ensure_default_seed()
    assert again == 0


@pytest.mark.asyncio
async def test_add_node_inherits_parent_depth(repositories, scope):
    audit = AuditService(
        repository=repositories["audit"],
        event_publisher=None,
        settings=CanonSettings(),
    )
    taxonomy = TaxonomyService(
        repository=repositories["taxonomy"],
        audit=audit,
        settings=CanonSettings(),
    )
    await taxonomy.ensure_default_seed()
    roots = await taxonomy.list_all()
    legal = next(r for r in roots if r.slug == Domain.legal.value)

    child = await taxonomy.add_node(
        CreateTaxonomyNodeRequest(
            parent_id=legal.id,
            slug="contracts",
            label="Contracts",
            domain=Domain.legal,
            description="Contract templates and clauses.",
        ),
        actor="tester",
        **scope,
    )
    assert child.depth == 1
    assert child.parent_id == legal.id
