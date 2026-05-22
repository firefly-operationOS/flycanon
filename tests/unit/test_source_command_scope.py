# Copyright 2026 Firefly Software Solutions Inc
"""Scope-requirement coverage for the source-intake CQRS commands.

The Plan 6 audit found that ``SubmitSourceCommand`` / ``ReplaceSourceCommand``
and ``AsyncIngestService.submit_async`` accepted optional ``tenant_id`` /
``workspace_id`` with a silent ``"default"`` fallback in the handlers. That
means a controller bug -- a forgotten scope kwarg on the request path --
would silently route every ingested source to the ``("default", "default")``
RLS bucket, invisible to the caller's real tenant/workspace under migration
0013's policies.

Both Command DTOs now require ``tenant_id`` / ``workspace_id`` (no default),
so a missing scope surfaces as a TypeError at construction time -- loud,
local, fixable at the controller. ``AsyncIngestService.submit_async`` follows
the same contract.
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
