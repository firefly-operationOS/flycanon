# Copyright 2026 Firefly Software Solutions Inc
"""Knowledge-quality services: stale + conflict detection."""

from __future__ import annotations

from flycanon.core.services.quality.conflict_detector import ConflictDetector
from flycanon.core.services.quality.stale_detector import StaleDetector

__all__ = ["ConflictDetector", "StaleDetector"]
