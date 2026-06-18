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

"""Query service -- hybrid search + RAG-answer over the corpus.

This package ``__init__`` is deliberately empty of eager service imports.
``AnswerService``/``SearchService`` transitively pull in ``httpx``, the model
client, and ``CanonSettings``; importing them here would drag that whole stack
into anything that touches the package -- including the hardened sandbox child
(:mod:`flycanon.core.services.query.rlm.sandbox.runner`), which is spawned with
a scrubbed env and must stay import-light (no secrets, no network). Import
services from their own modules instead (e.g.
``from flycanon.core.services.query.answer_service import AnswerService``).
"""

from __future__ import annotations
