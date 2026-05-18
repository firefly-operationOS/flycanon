# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge-item DTOs.

A knowledge item is the canonical, versioned unit downstream consumers
treat as ground truth. Every state transition is recorded as a new
:class:`KnowledgeVersion`; the :class:`KnowledgeItem` itself carries a
pointer to the current version and the lifecycle status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from flycanon.interfaces.enums import Domain, Jurisdiction, KnowledgeStatus


class Citation(BaseModel):
    """A pointer from a knowledge version to a source span.

    ``source_id`` identifies the source the citation refers to.
    ``chunk_id`` points at the specific chunk that backs the claim,
    when one applies. ``quote`` is the verbatim text the
    consolidation stage extracted; it is informative, not normative.
    """

    source_id: str
    chunk_id: str | None = Field(default=None)
    quote: str | None = Field(default=None, max_length=4000)
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    page: int | None = Field(default=None, ge=1, description="1-based page index, when applicable.")


class Provenance(BaseModel):
    """Resolved citation graph for a knowledge version.

    Returned by ``GET /api/v1/knowledge/{id}/provenance``. Carries
    every citation row, the source records they point at, and the
    full version chain so callers can reconstruct the
    why-this-is-canonical story without follow-up calls.
    """

    knowledge_item_id: str
    version: int
    citations: list[Citation]
    sources: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Compact source views (id, kind, title, content_sha256).",
    )
    history: list[KnowledgeVersion] = Field(default_factory=list)


class KnowledgeVersion(BaseModel):
    """A single revision of a knowledge item."""

    knowledge_item_id: str
    version: int = Field(ge=1)
    status: KnowledgeStatus
    title: str
    summary: str | None = Field(default=None)
    body: str = Field(description="Canonical content -- markdown is the recommended format.")
    domain: Domain
    jurisdiction: Jurisdiction = Field(default=Jurisdiction.GLOBAL)
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    supersedes_version: int | None = Field(default=None, ge=1)
    superseded_by_version: int | None = Field(default=None, ge=1)
    created_by: str | None = Field(default=None)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeItem(BaseModel):
    """Canonical pointer to the current version of a knowledge item."""

    id: str
    status: KnowledgeStatus
    current_version: int = Field(ge=1)
    title: str
    domain: Domain
    jurisdiction: Jurisdiction = Field(default=Jurisdiction.GLOBAL)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None = Field(default=None)
    summary: str | None = Field(default=None)


class KnowledgeItemsPage(BaseModel):
    """Paged list of knowledge items."""

    items: list[KnowledgeItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)


class CreateKnowledgeRequest(BaseModel):
    """Direct knowledge creation (bypassing the candidate pipeline).

    Use ``POST /api/v1/candidates/{id}:accept`` instead when the
    knowledge originates from a candidate proposal; this endpoint is
    for callers that own the canonical content already.
    """

    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1)
    summary: str | None = Field(default=None, max_length=4000)
    domain: Domain
    jurisdiction: Jurisdiction = Field(default=Jurisdiction.GLOBAL)
    tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    publish: bool = Field(
        default=True,
        description="Publish immediately. Set False to land the version in ``draft``.",
    )
    actor: str | None = Field(
        default=None,
        description="Stable identifier of the human or service performing the action.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateKnowledgeRequest(BaseModel):
    """Materialise a new draft / published version of an existing item.

    Every field is optional; whatever is sent overrides the
    corresponding field on the current version. The new version is
    appended to the history; the previous one transitions to
    ``superseded``.
    """

    title: str | None = Field(default=None, min_length=1, max_length=512)
    body: str | None = Field(default=None, min_length=1)
    summary: str | None = Field(default=None, max_length=4000)
    domain: Domain | None = Field(default=None)
    jurisdiction: Jurisdiction | None = Field(default=None)
    tags: list[str] | None = Field(default=None)
    citations: list[Citation] | None = Field(default=None)
    publish: bool = Field(default=True)
    actor: str | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


class SupersedeKnowledgeRequest(BaseModel):
    """Mark a knowledge item as superseded by another.

    The two items must share a domain (and ideally a jurisdiction);
    the supersession is recorded on both sides of the relationship
    and emitted as a ``KnowledgeItemSuperseded`` event.
    """

    superseded_by_item_id: str
    reason: str | None = Field(default=None, max_length=2000)
    actor: str | None = Field(default=None)


class RetireKnowledgeRequest(BaseModel):
    """Withdraw a knowledge item from circulation."""

    reason: str = Field(min_length=1, max_length=2000)
    actor: str | None = Field(default=None)


class FieldChange(BaseModel):
    """One field's old -> new transition between two versions."""

    field: str = Field(description="Name of the changed field.")
    before: Any | None = Field(default=None)
    after: Any | None = Field(default=None)


class KnowledgeVersionDiff(BaseModel):
    """Diff between two versions of the same knowledge item.

    Returned by ``GET /api/v1/knowledge/{id}/diff?from=X&to=Y``.
    The body diff is a Unix-style unified diff with three lines of
    context (the same shape ``git diff`` emits) so an audit tool or
    a UI can render it without parsing the wire form. Per-field
    changes cover the scalar columns that don't fit in a body diff
    (title, summary, domain, jurisdiction, tags). Citations are
    diffed at the set level since their order does not carry
    canonical meaning.
    """

    knowledge_item_id: str
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    body_diff: str = Field(
        description=(
            "Unified diff of the version body. Empty string when the "
            "two versions share an identical body."
        )
    )
    field_changes: list[FieldChange] = Field(
        default_factory=list,
        description="Scalar-field changes (title, summary, domain, jurisdiction, tags).",
    )
    citations_added: list[Citation] = Field(default_factory=list)
    citations_removed: list[Citation] = Field(default_factory=list)


Provenance.model_rebuild()
