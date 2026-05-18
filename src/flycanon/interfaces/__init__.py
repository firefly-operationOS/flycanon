# Copyright 2026 Firefly Software Solutions Inc
"""Public interface surface.

Every DTO and enum the REST API speaks lives here. Code outside this
package -- ORM entities, internal services, workers -- must not leak
into JSON responses; mappers convert between the internal model and
these DTOs.
"""

from __future__ import annotations
