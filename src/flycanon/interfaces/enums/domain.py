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

"""``Domain`` -- the operational area a knowledge item belongs to.

The default tree mirrors the stakeholder map from the workshop
synthesis that seeded the service:

* ``legal``      -- regulatory texts, contracts, official guidance.
* ``compliance`` -- internal compliance procedures, audit checklists.
* ``process``    -- operating procedures, runbooks, SOPs.
* ``network``    -- branch / retail / network operations playbooks.
* ``ai_platform``-- AI platform policy, model-use guardrails.
* ``executive``  -- strategy decks, board-level commitments.
* ``hr``         -- people-ops policies, training material.
* ``cto``        -- engineering charters, architecture decisions.
* ``engineering``-- design docs, RFCs, technical standards.
* ``security``   -- security policies, threat models, IR procedures.
* ``other``      -- escape hatch; should remain rare.

Callers can attach their own taxonomy nodes through
``/api/v1/taxonomy``; this enum just enumerates the canonical roots so
the wire contract stays stable.
"""

from __future__ import annotations

from enum import StrEnum


class Domain(StrEnum):
    legal = "legal"
    compliance = "compliance"
    process = "process"
    network = "network"
    ai_platform = "ai_platform"
    executive = "executive"
    hr = "hr"
    cto = "cto"
    engineering = "engineering"
    security = "security"
    other = "other"
