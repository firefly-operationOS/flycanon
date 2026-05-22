# Copyright 2026 Firefly Software Solutions Inc
"""Typed errors raised by the knowledge service.

Each subclass inherits from :class:`FireflyHTTPException` so the
conventions exception handler renders it as an RFC 7807
``ProblemDetail`` automatically. The legacy
``flycanon.web.problem_handlers`` still catches each subclass by
class during the Phase 4 migration window; once that file is
deleted in Task 2 the conventions handler picks them up via their
:class:`FireflyHTTPException` base.

``http_status`` is kept alongside the new ClassVar ``status`` so
the legacy parent handlers (``KnowledgeServiceError`` /
``ConsolidationError`` / ``IngestionError``) -- which read
``getattr(exc, "http_status", 400)`` -- continue to work during the
window. Both attributes are kept in sync.
"""

from __future__ import annotations

from typing import ClassVar

from flycanon.web.conventions import FireflyHTTPException


class KnowledgeServiceError(FireflyHTTPException):
    status: ClassVar[int] = 400
    code: ClassVar[str] = "knowledge_error"
    title: ClassVar[str] = "Knowledge service error"
    http_status: ClassVar[int] = 400


class KnowledgeItemNotFound(KnowledgeServiceError):
    status = 404
    code = "knowledge_item_not_found"
    title = "Knowledge item not found"
    http_status = 404

    def __init__(self, item_id: str) -> None:
        super().__init__(f"knowledge item {item_id!r} not found")
        self.item_id = item_id


class KnowledgeVersionNotFound(KnowledgeServiceError):
    status = 404
    code = "knowledge_version_not_found"
    title = "Knowledge version not found"
    http_status = 404

    def __init__(self, item_id: str, version: int) -> None:
        super().__init__(f"version {version} of knowledge item {item_id!r} not found")
        self.item_id = item_id
        self.version = version


class KnowledgeItemAlreadyRetired(KnowledgeServiceError):
    status = 409
    code = "knowledge_item_already_retired"
    title = "Knowledge item already retired"
    http_status = 409

    def __init__(self, item_id: str) -> None:
        super().__init__(f"knowledge item {item_id!r} is already retired")
        self.item_id = item_id


class InvalidSupersedeTarget(KnowledgeServiceError):
    status = 409
    code = "invalid_supersede_target"
    title = "Invalid supersede target"
    http_status = 409

    def __init__(self, item_id: str, target_id: str, detail: str) -> None:
        super().__init__(f"cannot supersede {item_id!r} with {target_id!r}: {detail}")
        self.item_id = item_id
        self.target_id = target_id


class KnowledgeVersionConflict(KnowledgeServiceError):
    """Another writer beat us to ``current_version + 1`` for this item.

    Surfaced when two concurrent ``PUT /api/v1/knowledge/{id}`` calls
    both compute the same next-version number and only one wins on
    insert. The losing caller should re-read the item (the version is
    now higher than they expected) and re-submit.
    """

    status = 409
    code = "knowledge_version_conflict"
    title = "Knowledge version conflict"
    http_status = 409

    def __init__(self, item_id: str, attempted_version: int) -> None:
        super().__init__(
            f"version {attempted_version} of knowledge item {item_id!r} "
            "was claimed by a concurrent writer; re-read and try again"
        )
        self.item_id = item_id
        self.attempted_version = attempted_version
