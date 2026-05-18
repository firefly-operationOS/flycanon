# Copyright 2026 Firefly Software Solutions Inc
"""flycanon Python SDK -- async client for the Operational Knowledge
Repository service."""

from __future__ import annotations

from flycanon_sdk._client import CanonClient
from flycanon_sdk._errors import CanonAPIError, CanonConnectionError
from flycanon_sdk._models import (
    AcceptCandidateRequest,
    AnswerRequest,
    AnswerResponse,
    AuditEvent,
    AuditPage,
    CandidateRecord,
    CandidatesPage,
    Citation,
    CreateKnowledgeRequest,
    CreateTaxonomyNodeRequest,
    Hit,
    KnowledgeItem,
    KnowledgeItemsPage,
    KnowledgeVersion,
    ProblemDetails,
    ProposeCandidateRequest,
    Provenance,
    RejectCandidateRequest,
    RetireKnowledgeRequest,
    SearchRequest,
    SearchResponse,
    SourceMetadata,
    SourceRecord,
    SourcesPage,
    SubmitSourceJsonPayload,
    SupersedeKnowledgeRequest,
    TaxonomyNode,
    TaxonomyTree,
    UpdateKnowledgeRequest,
    VersionInfo,
)

__version__ = "26.5.1"

__all__ = [
    "AcceptCandidateRequest",
    "AnswerRequest",
    "AnswerResponse",
    "AuditEvent",
    "AuditPage",
    "CanonAPIError",
    "CanonClient",
    "CanonConnectionError",
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
    "SubmitSourceJsonPayload",
    "SupersedeKnowledgeRequest",
    "TaxonomyNode",
    "TaxonomyTree",
    "UpdateKnowledgeRequest",
    "VersionInfo",
    "__version__",
]
