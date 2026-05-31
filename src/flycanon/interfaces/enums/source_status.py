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

"""``SourceStatus`` -- lifecycle of an ingested source.

State transitions are append-only and recorded in ``audit_events``.
Terminal states are ``ingested`` (success), ``failed`` (loader or
indexer rejected the bytes), and ``superseded`` (the same content hash
was re-uploaded under a different source id).
"""

from __future__ import annotations

from enum import StrEnum


class SourceStatus(StrEnum):
    pending = "pending"
    ingesting = "ingesting"
    ingested = "ingested"
    failed = "failed"
    superseded = "superseded"
