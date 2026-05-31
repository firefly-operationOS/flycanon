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

"""``IngestJobRow`` -> :class:`IngestJob` mapper."""

from __future__ import annotations

from flycanon.interfaces.dtos.job import IngestJob
from flycanon.models.entities.ingest_job import IngestJobRow


def to_ingest_job(row: IngestJobRow) -> IngestJob:
    return IngestJob(
        id=row.id,
        status=row.status,
        source_id=row.source_id,
        attempts=row.attempts or 0,
        filename=row.filename,
        content_type=row.content_type,
        uri=row.uri,
        content_sha256=row.content_sha256,
        actor=row.actor,
        correlation_id=row.correlation_id,
        callback_url=row.callback_url,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
    )
