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

"""``Jurisdiction`` -- the geographic / legal scope of a knowledge item.

Values use uppercase ISO 3166-1 alpha-2 country codes for the most
common single-country scopes, plus a small number of supranational
shorthands:

* ``GLOBAL`` -- applies everywhere; the default.
* ``EU``     -- European Union.
* ``ES``, ``DE``, ``FR``, ... -- single-country scope.
* ``LATAM``  -- the LATAM region.
* ``EMEA``   -- the EMEA region.

The list is not exhaustive; callers can attach a finer-grained
``jurisdiction_path`` (e.g. ``ES/Madrid``) via the optional ``scope``
field on the knowledge DTO.
"""

from __future__ import annotations

from enum import StrEnum


class Jurisdiction(StrEnum):
    GLOBAL = "GLOBAL"
    EU = "EU"
    LATAM = "LATAM"
    EMEA = "EMEA"
    APAC = "APAC"
    AMER = "AMER"
    ES = "ES"
    PT = "PT"
    FR = "FR"
    DE = "DE"
    IT = "IT"
    NL = "NL"
    UK = "UK"
    US = "US"
    MX = "MX"
    BR = "BR"
    AR = "AR"
    CO = "CO"
    CL = "CL"
