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

"""flycanon -- Operational Knowledge Repository service.

The single public symbol is :data:`__version__`. Importing the
top-level package does not boot the application; that is the job of
:mod:`flycanon.main` (ASGI) or :mod:`flycanon.cli` (CLI).
"""

from __future__ import annotations

__version__ = "26.5.6"

__all__ = ["__version__"]
