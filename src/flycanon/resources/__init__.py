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

"""Static resources packaged with the flycanon wheel.

Prompts, seed taxonomies, and any other read-only artefact the
runtime needs. Loaders should reach for files under this package via
``importlib.resources`` so the assets are reachable from inside the
distroless container.
"""

from __future__ import annotations
