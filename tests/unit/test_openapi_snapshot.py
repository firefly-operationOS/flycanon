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

"""OpenAPI snapshot gate.

Generates the current OpenAPI spec from the live FastAPI app and diffs
it against the committed ``openapi.json`` at the repo root. Any change
to a route, parameter, request body, response model, status code,
operationId, summary, tag or shared schema flips this gate red until
the snapshot is regenerated and committed alongside the code change.

The intent is to prevent silent SDK drift: callers that mvn / pip
install the published Java / Python SDKs (under ``sdks/``) bind to
this spec at code-gen time, so an unreviewed schema change ships to
clients with no audit trail. This test forces every spec change to
land in a reviewable diff.

Regenerate the snapshot with::

    uv run python scripts/update_openapi_snapshot.py

Generation matches the production lifespan flow: pyfly's startup
populates the bean context, then
:func:`flycanon.web.openapi_override.install_openapi` replaces
FastAPI's lazy-shim spec with the introspector-driven one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Mirror the env defaults the regenerator script + CI ``unit`` job
# set. Done at module-import time -- ``flycanon.main`` runs
# ``create_app`` at import, so these MUST land before that import.
os.environ.setdefault("FLYCANON_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("FLYCANON_EDA_ADAPTER", "memory")
os.environ.setdefault("RUN_MIGRATIONS", "false")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dummy-key")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


async def _load_app_openapi() -> dict:
    """Boot the app, install the pyfly OpenAPI generator, return the spec.

    Mirrors the ASGI lifespan flow exactly:

    1. ``await _pyfly.startup()`` populates the bean context the
       generator walks.
    2. ``install_openapi(app, context, ...)`` swaps FastAPI's
       lazy-shim ``app.openapi`` callable for pyfly's introspector.
    3. ``app.openapi()`` renders + caches the spec.

    ``shutdown()`` runs in a ``finally`` block so a failed assert
    doesn't leak the bean context across test modules.
    """
    from flycanon import __version__
    from flycanon.main import _DESCRIPTION, _TITLE, _pyfly, app
    from flycanon.web.openapi_override import install_openapi

    await _pyfly.startup()
    try:
        install_openapi(
            app,
            _pyfly.context,
            title=_TITLE,
            version=__version__,
            description=_DESCRIPTION,
        )
        # Clear the FastAPI openapi cache so the pyfly override runs
        # instead of returning whatever a previous lifespan installed.
        app.openapi_schema = None
        return app.openapi()
    finally:
        await _pyfly.shutdown()


async def test_openapi_snapshot_matches() -> None:
    """The live spec equals the committed snapshot byte-for-byte (as JSON)."""
    snapshot_path = _repo_root() / "openapi.json"
    if not snapshot_path.exists():
        pytest.fail(
            f"openapi.json snapshot missing at {snapshot_path}. "
            "Run: uv run python scripts/update_openapi_snapshot.py"
        )
    current = await _load_app_openapi()
    expected = json.loads(snapshot_path.read_text())
    assert current == expected, (
        "OpenAPI spec drifted from the committed snapshot at "
        f"{snapshot_path}. If this drift is intentional, regenerate with:\n"
        "    uv run python scripts/update_openapi_snapshot.py\n"
        "and commit the resulting openapi.json change alongside the route "
        "change in the same PR."
    )
