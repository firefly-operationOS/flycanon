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

"""Unit tests for the FLYCANON_RLM_SANDBOX config knobs."""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings


def test_rlm_sandbox_defaults_to_subprocess():
    settings = CanonSettings()
    assert settings.rlm_sandbox == "subprocess"
    assert settings.rlm_sandbox_timeout_s == 30


def test_rlm_sandbox_accepts_inprocess_optout():
    assert CanonSettings(rlm_sandbox="inprocess").rlm_sandbox == "inprocess"


@pytest.mark.parametrize("value", ["INPROCESS", " inprocess ", "Inprocess"])
def test_rlm_sandbox_normalises_case_and_whitespace(value):
    assert CanonSettings(rlm_sandbox=value).rlm_sandbox == "inprocess"


@pytest.mark.parametrize("value", ["subprocess", "nonsense", "", "thread"])
def test_rlm_sandbox_unknown_falls_back_to_subprocess(value):
    assert CanonSettings(rlm_sandbox=value).rlm_sandbox == "subprocess"


def test_rlm_sandbox_timeout_rejects_below_one():
    with pytest.raises(ValueError):
        CanonSettings(rlm_sandbox_timeout_s=0)


def test_rlm_sandbox_reads_from_env(monkeypatch):
    monkeypatch.setenv("FLYCANON_RLM_SANDBOX", "inprocess")
    monkeypatch.setenv("FLYCANON_RLM_SANDBOX_TIMEOUT_S", "45")
    settings = CanonSettings()
    assert settings.rlm_sandbox == "inprocess"
    assert settings.rlm_sandbox_timeout_s == 45
