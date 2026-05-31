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

"""``WorkspaceStatus`` -- workspace lifecycle status.

Mirrors the values written to the ``canon_workspaces.status`` column
and exposed on the ``WorkspaceSpec`` DTO. The values are part of the
public API contract -- renaming a member is a breaking change.

* ``draft``      -- the workspace exists but is not yet active; no
                    knowledge ingestion or retrieval should target it.
* ``active``     -- the default working state; reads and writes are
                    permitted.
* ``closed``     -- the workspace is no longer accepting writes;
                    reads remain available for audit and traceability.
* ``handed_off`` -- ownership transferred to another tenant or
                    workspace; the row is preserved for provenance but
                    is otherwise inert.
"""

from __future__ import annotations

from enum import StrEnum


class WorkspaceStatus(StrEnum):
    draft = "draft"
    active = "active"
    closed = "closed"
    handed_off = "handed_off"
