# Copyright 2026 Firefly Software Solutions Inc
"""``canon_taxonomy_nodes`` -- domain / jurisdiction tree.

The default seed inserts one root node per :class:`Domain` value at
``depth=0``. Callers attach finer-grained children at runtime; the
``depth`` column lets the tree query return rows in breadth-first
order without a recursive CTE.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flycanon.models.entities.base import Base


class TaxonomyNodeRow(Base):
    __tablename__ = "canon_taxonomy_nodes"

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
    parent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("canon_taxonomy_nodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("parent_id", "slug", name="uq_canon_taxonomy_parent_slug"),
        Index("ix_canon_taxonomy_domain_depth", "domain", "depth"),
        Index("ix_canon_taxonomy_nodes_tenant_workspace", "tenant_id", "workspace_id"),
    )
