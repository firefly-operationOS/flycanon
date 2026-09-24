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

"""Every controller resolves from the booted container.

pyfly's controllers are lazy: the bean graph behind a route is built
on the FIRST request to it, not at boot. A missing ``@service`` on a
collaborator therefore boots cleanly, passes every unit test that
constructs the class directly, renders the OpenAPI snapshot -- and
turns every request to that route into ``502 NoSuchBeanError``. That
is exactly what happened while closing the 26.7.1 skeptic round: a
dataclass inserted between ``@service`` and ``class IntakeService``
took the decorator with it, and ``POST /api/v1/sources`` answered 502
on the rebuilt image before a single assertion had failed.

This test boots the application the way the snapshot test does and
resolves every stereotype-decorated controller under
``flycanon.web.controllers``, which pulls the whole service graph
behind the routes. It is the boot-time check pyfly does not do.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil

import pytest

os.environ.setdefault("FLYCANON_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("FLYCANON_EDA_ADAPTER", "memory")
os.environ.setdefault("RUN_MIGRATIONS", "false")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dummy-key")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

_CONTROLLER_STEREOTYPES = frozenset({"controller", "rest_controller"})


def _controller_classes() -> list[type]:
    package = importlib.import_module("flycanon.web.controllers")
    found: list[type] = []
    for _importer, modname, _ispkg in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + "."):
        module = importlib.import_module(modname)
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if getattr(obj, "__pyfly_stereotype__", "") in _CONTROLLER_STEREOTYPES:
                found.append(obj)
    return found


@pytest.mark.asyncio
async def test_every_controller_resolves_from_the_booted_container() -> None:
    # A fresh application, not ``flycanon.main._pyfly``: that module-level
    # singleton is booted by the OpenAPI snapshot test in the same
    # process, and pyfly's context does not survive a second startup.
    from pyfly.core import PyFlyApplication

    from flycanon.app import CanonApplication

    controllers = _controller_classes()
    assert len(controllers) >= 10, [c.__name__ for c in controllers]

    pyfly_app = PyFlyApplication(CanonApplication)
    await pyfly_app.startup()
    try:
        container = pyfly_app.context.container
        unresolved: dict[str, str] = {}
        for cls in controllers:
            try:
                container.resolve(cls)
            except Exception as exc:  # noqa: BLE001 -- collect every failure, then report once
                unresolved[cls.__name__] = str(exc).splitlines()[0]
        assert not unresolved, unresolved
    finally:
        await pyfly_app.shutdown()


def test_intake_service_is_the_registered_bean_of_its_module() -> None:
    """The regression itself, pinned at the module level.

    ``SourceRemoval`` lives above ``IntakeService`` in the same module;
    the stereotype must be on the service, not on the result type.
    """
    from pyfly.container.scanner import scan_module_classes

    module = importlib.import_module("flycanon.core.services.sources.intake_service")
    assert [c.__name__ for c in scan_module_classes(module)] == ["IntakeService"]
    assert not getattr(module.SourceRemoval, "__pyfly_injectable__", False)
