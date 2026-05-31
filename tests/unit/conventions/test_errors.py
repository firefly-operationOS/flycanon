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

"""``ProblemDetail`` is the unified RFC 7807 envelope.

The existing ``flyradar.interfaces.dtos.error.ProblemDetail`` will
be replaced by this one when controllers are wired in the next
plan. This test locks the new shape: ``type`` is the URI,
``code`` is the machine-readable identifier, ``title`` is human.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from flycanon.web.conventions.errors import ProblemDetail


def test_minimal_problem_detail() -> None:
    pd = ProblemDetail(
        type="https://firefly.dev/problems/workspace_not_found",
        code="workspace_not_found",
        title="Workspace not found",
        status=404,
    )
    assert pd.type == "https://firefly.dev/problems/workspace_not_found"
    assert pd.code == "workspace_not_found"
    assert pd.title == "Workspace not found"
    assert pd.status == 404
    assert pd.detail is None
    assert pd.instance is None
    assert pd.correlation_id is None
    assert pd.errors == []


def test_full_problem_detail_json_round_trip() -> None:
    pd = ProblemDetail(
        type="https://firefly.dev/problems/invalid_request",
        code="invalid_request",
        title="Invalid request",
        status=422,
        detail="Field 'name' is required.",
        instance="/api/v1/workspaces",
        correlation_id="01J5XYZ",
        errors=[{"code": "missing", "path": "name", "message": "Field is required."}],
    )
    payload = pd.model_dump_json()
    restored = ProblemDetail.model_validate_json(payload)
    assert restored == pd
    assert json.loads(payload)["code"] == "invalid_request"


def test_problem_detail_is_frozen() -> None:
    pd = ProblemDetail(
        type="https://firefly.dev/problems/x",
        code="x",
        title="X",
        status=400,
    )
    with pytest.raises(ValidationError):
        pd.status = 500  # type: ignore[misc]


def test_problem_detail_rejects_out_of_range_status() -> None:
    with pytest.raises(ValidationError):
        ProblemDetail(
            type="https://firefly.dev/problems/x",
            code="x",
            title="X",
            status=99,
        )


def test_problem_detail_rejects_non_snake_case_code() -> None:
    with pytest.raises(ValidationError):
        ProblemDetail(
            type="https://firefly.dev/problems/x",
            code="Workspace-Not-Found",
            title="X",
            status=404,
        )
