# Copyright 2026 Firefly Software Solutions Inc
"""Public DTOs -- the JSON shapes flycanon speaks over the wire."""

from __future__ import annotations

from flycanon.interfaces.dtos.audit import AuditEvent, AuditPage
from flycanon.interfaces.dtos.candidate import (
    AcceptCandidateRequest,
    CandidateRecord,
    CandidatesPage,
    ProposeCandidateRequest,
    RejectCandidateRequest,
)
from flycanon.interfaces.dtos.error import ProblemDetails
from flycanon.interfaces.dtos.knowledge import (
    Citation,
    CreateKnowledgeRequest,
    KnowledgeItem,
    KnowledgeItemsPage,
    KnowledgeVersion,
    Provenance,
    RetireKnowledgeRequest,
    SupersedeKnowledgeRequest,
    UpdateKnowledgeRequest,
)
from flycanon.interfaces.dtos.query import (
    AnswerRequest,
    AnswerResponse,
    Hit,
    SearchRequest,
    SearchResponse,
)
from flycanon.interfaces.dtos.source import (
    SourceMetadata,
    SourceRecord,
    SourcesPage,
    SubmitSourceRequest,
)
from flycanon.interfaces.dtos.taxonomy import (
    CreateTaxonomyNodeRequest,
    TaxonomyNode,
    TaxonomyTree,
)
from flycanon.interfaces.dtos.version import VersionInfo
from flycanon.interfaces.dtos.workspace import (
    WorkspaceCreate,
    WorkspaceSpec,
    WorkspaceSummary,
    WorkspaceUpdate,
)

__all__ = [
    "AcceptCandidateRequest",
    "AnswerRequest",
    "AnswerResponse",
    "AuditEvent",
    "AuditPage",
    "CandidateRecord",
    "CandidatesPage",
    "Citation",
    "CreateKnowledgeRequest",
    "CreateTaxonomyNodeRequest",
    "Hit",
    "KnowledgeItem",
    "KnowledgeItemsPage",
    "KnowledgeVersion",
    "ProblemDetails",
    "ProposeCandidateRequest",
    "Provenance",
    "RejectCandidateRequest",
    "RetireKnowledgeRequest",
    "SearchRequest",
    "SearchResponse",
    "SourceMetadata",
    "SourceRecord",
    "SourcesPage",
    "SubmitSourceRequest",
    "SupersedeKnowledgeRequest",
    "TaxonomyNode",
    "TaxonomyTree",
    "UpdateKnowledgeRequest",
    "VersionInfo",
    "WorkspaceCreate",
    "WorkspaceSpec",
    "WorkspaceSummary",
    "WorkspaceUpdate",
]
