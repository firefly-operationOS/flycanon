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

"""Scope-requirement coverage for the source-intake CQRS commands.

``SubmitSourceCommand`` / ``ReplaceSourceCommand`` and
``AsyncIngestService.submit_async`` require ``tenant_id`` /
``workspace_id`` with no default. A controller bug -- a forgotten
scope kwarg on the request path -- would otherwise silently route
every ingested source to the ``("default", "default")`` RLS bucket,
invisible to the caller's real tenant/workspace under the RLS
policies.

Both Command DTOs require ``tenant_id`` / ``workspace_id`` (no
default), so a missing scope surfaces as a TypeError at construction
time -- loud, local, fixable at the controller.
``AsyncIngestService.submit_async`` follows the same contract.
"""

from __future__ import annotations

import inspect

import pytest

from flycanon.core.services.sources.async_ingest_service import AsyncIngestService
from flycanon.core.services.sources.replace_source_handler import ReplaceSourceCommand
from flycanon.core.services.sources.submit_source_handler import SubmitSourceCommand


class TestSubmitSourceCommandRequiresScope:
    def test_construction_without_tenant_id_raises(self) -> None:
        # A controller that forgets to thread ``tenant_id`` must fail
        # at the Command boundary, not silently land in ('default', 'default').
        with pytest.raises(TypeError):
            SubmitSourceCommand(content=b"x", workspace_id="ws-A")  # type: ignore[call-arg]

    def test_construction_without_workspace_id_raises(self) -> None:
        with pytest.raises(TypeError):
            SubmitSourceCommand(content=b"x", tenant_id="acme")  # type: ignore[call-arg]

    def test_construction_with_full_scope_succeeds(self) -> None:
        cmd = SubmitSourceCommand(content=b"x", tenant_id="acme", workspace_id="ws-A")
        assert cmd.tenant_id == "acme"
        assert cmd.workspace_id == "ws-A"

    def test_scope_fields_have_no_default(self) -> None:
        # Dataclass parameter introspection: the scope fields must be
        # required positional/keyword arguments (no MISSING default).
        from dataclasses import MISSING, fields

        scope_fields = {f.name: f for f in fields(SubmitSourceCommand)}
        assert scope_fields["tenant_id"].default is MISSING
        assert scope_fields["tenant_id"].default_factory is MISSING
        assert scope_fields["workspace_id"].default is MISSING
        assert scope_fields["workspace_id"].default_factory is MISSING


class TestReplaceSourceCommandRequiresScope:
    def test_construction_without_tenant_id_raises(self) -> None:
        with pytest.raises(TypeError):
            ReplaceSourceCommand(
                source_id="s1",
                content=b"x",
                workspace_id="ws-A",  # type: ignore[call-arg]
            )

    def test_construction_without_workspace_id_raises(self) -> None:
        with pytest.raises(TypeError):
            ReplaceSourceCommand(
                source_id="s1",
                content=b"x",
                tenant_id="acme",  # type: ignore[call-arg]
            )

    def test_construction_with_full_scope_succeeds(self) -> None:
        cmd = ReplaceSourceCommand(
            source_id="s1",
            content=b"x",
            tenant_id="acme",
            workspace_id="ws-A",
        )
        assert cmd.tenant_id == "acme"
        assert cmd.workspace_id == "ws-A"


class TestAsyncIngestServiceSubmitAsyncRequiresScope:
    def test_submit_async_signature_has_no_scope_default(self) -> None:
        # Same guarantee at the service layer: ``submit_async`` must
        # not soft-default scope. A controller bug surfaces as a
        # TypeError at call time.
        sig = inspect.signature(AsyncIngestService.submit_async)
        params = sig.parameters
        assert params["tenant_id"].default is inspect.Parameter.empty, (
            "AsyncIngestService.submit_async must not default tenant_id -- "
            "forgotten scope silently landed in ('default','default')."
        )
        assert params["workspace_id"].default is inspect.Parameter.empty, (
            "AsyncIngestService.submit_async must not default workspace_id -- "
            "forgotten scope silently landed in ('default','default')."
        )
