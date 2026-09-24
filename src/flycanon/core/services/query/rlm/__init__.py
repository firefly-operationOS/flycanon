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

"""Recursive Language Model (RLM) query engine.

A self-contained CodeAct REPL plus the synchronous chat clients it drives.
The engine is agnostic in two directions:

* **corpus** -- it never imports a concrete document store; the corpus is
  duck-typed (see :class:`flycanon.core.services.query.rlm.session.DocCorpus`).
* **provider** -- :mod:`~flycanon.core.services.query.rlm.chat` holds the
  ``RlmChatClient`` protocol and the factory that resolves
  ``FLYCANON_RLM_ROOT_MODEL`` to
  :class:`~flycanon.core.services.query.rlm.client.AnthropicClient` or
  :class:`~flycanon.core.services.query.rlm.azure_client.AzureOpenAIChatClient`.
"""
