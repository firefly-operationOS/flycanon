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

"""``CandidateStatus`` -- pre-canonical proposal lifecycle.

Candidates are emitted by the consolidation stage; humans (or an
automated policy) accept, reject, or merge them into existing
knowledge items.

* ``proposed`` -- emitted by the consolidator, awaiting decision.
* ``accepted`` -- materialised as a new knowledge version. The
                  ``materialised_knowledge_item_id`` field on the
                  Candidate DTO points to the resulting item.
* ``rejected`` -- discarded with a free-form reason.
* ``merged``   -- folded into an existing knowledge item as an
                  additional citation or evidence row, without
                  creating a new version.
"""

from __future__ import annotations

from enum import StrEnum


class CandidateStatus(StrEnum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    merged = "merged"
