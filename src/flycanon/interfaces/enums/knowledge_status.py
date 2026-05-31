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

"""``KnowledgeStatus`` -- canonical-item lifecycle.

Each knowledge item points at the current version; every transition is
recorded as a new version row, never an in-place mutation. The status
on the *item* mirrors the status on the *current version*.

* ``draft``      -- the version exists but is not yet validated; no
                    downstream consumer should treat it as authoritative.
* ``published``  -- the current version is canonical. Retrievers return
                    chunks from published items by default.
* ``superseded`` -- a newer version is canonical; this version is
                    retained for historical traceability but never
                    surfaced as a hit.
* ``retired``    -- the entire item (every version) is withdrawn; no
                    longer indexed and no longer cited.
"""

from __future__ import annotations

from enum import StrEnum


class KnowledgeStatus(StrEnum):
    draft = "draft"
    published = "published"
    superseded = "superseded"
    retired = "retired"
