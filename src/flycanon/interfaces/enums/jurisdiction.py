# Copyright 2026 Firefly Software Solutions Inc
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
