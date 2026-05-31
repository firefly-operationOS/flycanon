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

"""Typed errors for the ingestion pipeline.

Each subclass inherits from :class:`FireflyHTTPException` so the
conventions exception handler renders it as an RFC 7807
``ProblemDetail`` automatically.
"""

from __future__ import annotations

from typing import ClassVar

from flycanon.web.conventions import FireflyHTTPException


class IngestionError(FireflyHTTPException):
    """Base class for every ingestion failure.

    Subclasses carry a stable ``code`` attribute the conventions
    handler maps to ``ProblemDetail.code`` so SDKs can branch on the
    failure mode without parsing ``detail``.
    """

    status: ClassVar[int] = 422
    code: ClassVar[str] = "ingestion_failed"
    title: ClassVar[str] = "Source ingestion failed"


class UnsupportedSourceKind(IngestionError):
    """Raised when the requested ``SourceKind`` has no registered loader."""

    status = 415
    code = "unsupported_source_kind"
    title = "Unsupported source kind"

    def __init__(self, kind: str) -> None:
        super().__init__(f"no loader is registered for source kind {kind!r}")
        self.kind = kind


class EmptySource(IngestionError):
    """Raised when the loader produces no text content."""

    status = 422
    code = "empty_source"
    title = "Empty source"

    def __init__(self) -> None:
        super().__init__("the source produced no extractable text content")


class CorruptSource(IngestionError):
    """Raised when the loader fails to parse the bytes."""

    status = 422
    code = "corrupt_source"
    title = "Corrupt source"

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"could not parse {kind!r} source: {detail}")
        self.kind = kind
