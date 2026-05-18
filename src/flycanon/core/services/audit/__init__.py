# Copyright 2026 Firefly Software Solutions Inc
"""Audit log -- append-only mirror of every state change.

Every mutation goes through :class:`AuditService.record`, which
persists a row to ``canon_audit_events`` and broadcasts the same
payload on the ``flycanon.audit`` EDA topic so compliance projections
see the change without polling.
"""

from __future__ import annotations

from flycanon.core.services.audit.audit_service import AuditService

__all__ = ["AuditService"]
