# Copyright 2026 Firefly Software Solutions Inc
"""Typed errors raised by the knowledge service.

Each subclass carries a stable ``code`` the exception advice maps to
``ProblemDetails.code``.
"""

from __future__ import annotations


class KnowledgeServiceError(Exception):
    code: str = "knowledge_error"
    http_status: int = 400


class KnowledgeItemNotFound(KnowledgeServiceError):
    code = "knowledge_item_not_found"
    http_status = 404

    def __init__(self, item_id: str) -> None:
        super().__init__(f"knowledge item {item_id!r} not found")
        self.item_id = item_id


class KnowledgeVersionNotFound(KnowledgeServiceError):
    code = "knowledge_version_not_found"
    http_status = 404

    def __init__(self, item_id: str, version: int) -> None:
        super().__init__(f"version {version} of knowledge item {item_id!r} not found")
        self.item_id = item_id
        self.version = version


class KnowledgeItemAlreadyRetired(KnowledgeServiceError):
    code = "knowledge_item_already_retired"
    http_status = 409

    def __init__(self, item_id: str) -> None:
        super().__init__(f"knowledge item {item_id!r} is already retired")
        self.item_id = item_id


class InvalidSupersedeTarget(KnowledgeServiceError):
    code = "invalid_supersede_target"
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

    code = "knowledge_version_conflict"
    http_status = 409

    def __init__(self, item_id: str, attempted_version: int) -> None:
        super().__init__(
            f"version {attempted_version} of knowledge item {item_id!r} "
            "was claimed by a concurrent writer; re-read and try again"
        )
        self.item_id = item_id
        self.attempted_version = attempted_version
