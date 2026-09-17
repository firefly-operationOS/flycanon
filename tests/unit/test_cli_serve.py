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

"""``flycanon serve`` / ``flycanon worker`` process contracts.

* ``serve`` binds the configured port and tells pyfly about it: pyfly
  v26.09 logs ``server_started`` from the ``_PYFLY_SERVER_*`` variables
  its own ``pyfly run`` exports; ``flycanon serve`` starts uvicorn
  directly, so it must export the same contract or the boot log reports
  ``port=8080`` while the socket is on ``FLYCANON_PORT``.
* ``worker`` drains its OWN consumer group. pyfly subscribes a
  cache-invalidation bridge on ``*`` in every process, so an API that
  shared the worker's group advanced the outbox cursor past the
  ``IngestSourceRequested`` events and async jobs never ran.
* ``worker`` runs no server, and pyfly logs ``server_started``
  unconditionally with ``0.0.0.0:8080`` as the fallback -- so the
  worker exports ``server=none host=- port=0`` rather than letting the
  log claim a listener it does not hold.
"""

from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

import pytest

from flycanon import cli
from flycanon.config import get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("_PYFLY_SERVER_TYPE", "_PYFLY_SERVER_HOST", "_PYFLY_SERVER_PORT", "FLYCANON_EDA_GROUP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLYCANON_PORT", "8765")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_serve_runs_uvicorn_on_the_configured_port_and_exports_pyfly_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def _run(app: str, **kwargs) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=_run))

    assert cli.cmd_serve(argparse.Namespace()) == 0

    assert calls == [{"app": "flycanon.main:app", "host": "0.0.0.0", "port": 8765, "log_level": "info"}]
    assert os.environ["_PYFLY_SERVER_PORT"] == "8765"
    assert os.environ["_PYFLY_SERVER_HOST"] == "0.0.0.0"
    assert os.environ["_PYFLY_SERVER_TYPE"] == "uvicorn"


def test_worker_defaults_its_own_eda_group_but_respects_an_explicit_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.ensure_worker_eda_group() == "flycanon-workers"
    assert os.environ["FLYCANON_EDA_GROUP"] == "flycanon-workers"
    monkeypatch.setenv("FLYCANON_EDA_GROUP", "flycanon-workers-eu")
    assert cli.ensure_worker_eda_group() == "flycanon-workers-eu"


def test_api_and_worker_groups_differ_by_default() -> None:
    """pyfly.yaml's interpolation default for the API must not equal the worker's."""
    from pathlib import Path

    yaml_text = (Path(cli.__file__).resolve().parents[2] / "pyfly.yaml").read_text()
    assert "group: ${FLYCANON_EDA_GROUP:flycanon-api}" in yaml_text
    assert cli.WORKER_EDA_GROUP != "flycanon-api"


def test_worker_exports_a_no_listener_server_contract() -> None:
    """The worker holds no socket; pyfly's boot line must not say 0.0.0.0:8080."""
    cli.ensure_worker_server_contract()
    assert os.environ["_PYFLY_SERVER_TYPE"] == "none"
    assert os.environ["_PYFLY_SERVER_HOST"] == "-"
    assert os.environ["_PYFLY_SERVER_PORT"] == "0"
    # pyfly casts the port with int(); the contract must survive that.
    assert int(os.environ["_PYFLY_SERVER_PORT"]) == 0


def test_server_contract_respects_an_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_PYFLY_SERVER_PORT", "9999")
    cli.export_server_contract(server_type="uvicorn", host="0.0.0.0", port=8765)
    assert os.environ["_PYFLY_SERVER_PORT"] == "9999"
    assert os.environ["_PYFLY_SERVER_TYPE"] == "uvicorn"


def test_worker_boot_line_is_what_pyfly_will_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive pyfly's own ``_log_server_info`` with the worker contract and read the line back."""
    from pyfly.core.application import PyFlyApplication

    cli.ensure_worker_server_contract()
    fields: dict = {}
    app = PyFlyApplication.__new__(PyFlyApplication)
    app._logger = SimpleNamespace(info=lambda event, **kw: fields.update({"event": event, **kw}))  # type: ignore[attr-defined]
    PyFlyApplication._log_server_info(app)
    assert fields["event"] == "server_started"
    assert (fields["server"], fields["host"], fields["port"]) == ("none", "-", 0)
