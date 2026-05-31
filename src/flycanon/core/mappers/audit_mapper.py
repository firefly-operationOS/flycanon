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

"""``AuditEventRow`` -> :class:`AuditEvent` DTO."""

from __future__ import annotations

from flycanon.interfaces.dtos.audit import AuditEvent
from flycanon.models.entities.audit_event import AuditEventRow


def to_audit_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        occurred_at=row.occurred_at,
        event_type=row.event_type,
        actor=row.actor,
        subject_id=row.subject_id,
        subject_kind=row.subject_kind,
        correlation_id=row.correlation_id,
        payload=dict(row.payload_json or {}),
    )
