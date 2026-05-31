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

"""Source intake orchestration + CQRS handlers."""

from __future__ import annotations

from flycanon.core.services.sources.errors import SourceNotFound
from flycanon.core.services.sources.get_source_handler import (
    GetSourceHandler,
    GetSourceQuery,
)
from flycanon.core.services.sources.intake_service import IntakeService
from flycanon.core.services.sources.list_sources_handler import (
    ListSourcesHandler,
    ListSourcesQuery,
)
from flycanon.core.services.sources.replace_source_handler import (
    ReplaceSourceCommand,
    ReplaceSourceHandler,
)
from flycanon.core.services.sources.submit_source_handler import (
    SubmitSourceCommand,
    SubmitSourceHandler,
)

__all__ = [
    "GetSourceHandler",
    "GetSourceQuery",
    "IntakeService",
    "ListSourcesHandler",
    "ListSourcesQuery",
    "ReplaceSourceCommand",
    "ReplaceSourceHandler",
    "SourceNotFound",
    "SubmitSourceCommand",
    "SubmitSourceHandler",
]
