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

"""flycanon web tier -- REST controllers + global exception advice.

The web tier is the only thing that speaks
``application/json`` over HTTP. It depends on
:class:`pyfly.cqrs.CommandBus` / :class:`pyfly.cqrs.QueryBus` and the
DTOs in :mod:`flycanon.interfaces.dtos`; it must not import from
``flycanon.core.services.*`` directly.
"""

from __future__ import annotations
