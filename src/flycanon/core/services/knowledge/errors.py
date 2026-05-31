# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Typed errors raised by the knowledge service.

Each subclass inherits from :class:`FireflyHTTPException` so the
conventions exception handler renders it as an RFC 7807
``ProblemDetail`` automatically.
"""

from __future__ import annotations

from typing import ClassVar

from flycanon.web.conventions import FireflyHTTPException


class KnowledgeServiceError(FireflyHTTPException):
    status: ClassVar[int] = 400
    code: ClassVar[str] = "knowledge_error"
    title: ClassVar[str] = "Knowledge service error"


class KnowledgeItemNotFound(KnowledgeServiceError):
    status = 404
    code = "knowledge_item_not_found"
    title = "Knowledge item not found"

    def __init__(self, item_id: str) -> None:
        super().__init__(f"knowledge item {item_id!r} not found")
        self.item_id = item_id


class KnowledgeVersionNotFound(KnowledgeServiceError):
    status = 404
    code = "knowledge_version_not_found"
    title = "Knowledge version not found"

    def __init__(self, item_id: str, version: int) -> None:
        super().__init__(f"version {version} of knowledge item {item_id!r} not found")
        self.item_id = item_id
        self.version = version


class KnowledgeItemAlreadyRetired(KnowledgeServiceError):
    status = 409
    code = "knowledge_item_already_retired"
    title = "Knowledge item already retired"

    def __init__(self, item_id: str) -> None:
        super().__init__(f"knowledge item {item_id!r} is already retired")
        self.item_id = item_id


class InvalidSupersedeTarget(KnowledgeServiceError):
    status = 409
    code = "invalid_supersede_target"
    title = "Invalid supersede target"

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

    def __init__(self, item_id: str, attempted_version: int) -> None:
        super().__init__(
            f"version {attempted_version} of knowledge item {item_id!r} "
            "was claimed by a concurrent writer; re-read and try again"
        )
        self.item_id = item_id
        self.attempted_version = attempted_version
