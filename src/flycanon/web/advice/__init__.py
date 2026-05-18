# Copyright 2026 Firefly Software Solutions Inc
"""flycanon global ``@controller_advice`` handlers.

Maps domain exceptions to RFC 7807 problem details so the API speaks
``application/problem+json`` end-to-end.
"""

from __future__ import annotations
