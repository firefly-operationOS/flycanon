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

"""Typed exceptions for the source-intake surface."""

from __future__ import annotations


class SourceNotFound(Exception):
    """Raised when a source id is unknown to the canonical store.

    Maps to RFC 7807 ``404 source_not_found`` through the controller
    advice. Used by the replace + (future) delete flows.
    """

    code = "source_not_found"
    http_status = 404

    def __init__(self, source_id: str) -> None:
        super().__init__(f"source {source_id!r} not found")
        self.source_id = source_id
