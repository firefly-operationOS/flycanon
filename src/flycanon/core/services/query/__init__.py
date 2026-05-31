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

"""Query service -- hybrid search + RAG-answer over the corpus."""

from __future__ import annotations

from flycanon.core.services.query.answer_service import AnswerOutput, AnswerService
from flycanon.core.services.query.search_service import SearchService

__all__ = ["AnswerOutput", "AnswerService", "SearchService"]
