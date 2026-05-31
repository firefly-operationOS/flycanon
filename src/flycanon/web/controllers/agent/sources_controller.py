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

"""Agent-tier source endpoints (``/api/v1/agent/sources``).

Two routes mounted under ``/api/v1/agent/sources``:

* ``POST /api/v1/agent/sources``       -- submit a source for
  intake. Scope: ``agent.sources:ingest``. ``Idempotency-Key`` is
  **mandatory** (missing key -> ``400 missing_idempotency_key``).
* ``GET /api/v1/agent/sources/{source_id}`` -- read a single source
  record. Scope: ``agent.sources:read``.

Both routes reuse the **same** CQRS commands / queries the
user-tier :class:`SourcesController` dispatches
(:class:`SubmitSourceCommand`, :class:`GetSourceQuery`) -- the only
delta is the auth layer (``X-Agent-Token`` replaces the operator
JWT) and the mandatory idempotency key on the POST.

Pyfly's ``@rest_controller`` does NOT resolve FastAPI ``Depends()``
defaults, so we inject the raw Starlette ``Request`` and run
:meth:`AgentTokenService.verify` from inside each handler via the
shared :func:`verify_agent_token` helper.
"""

from __future__ import annotations

import base64
import logging

from pyfly.container import rest_controller
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.observability.correlation import get_correlation_id
from pyfly.web import (
    Body,
    PathVar,
    Valid,
    get_mapping,
    post_mapping,
    request_mapping,
)
from starlette.requests import Request

from flycanon.core.services.auth.agent_token_service import AgentTokenService
from flycanon.core.services.sources import (
    GetSourceQuery,
    SubmitSourceCommand,
)
from flycanon.core.services.sources.url_fetcher import UrlFetcher
from flycanon.interfaces.dtos.source import SourceRecord
from flycanon.web.controllers.agent._helpers import (
    check_idempotency_replay,
    store_idempotent_response,
    verify_agent_token,
)
from flycanon.web.controllers.sources_controller import SubmitSourceJsonPayload
from flycanon.web.conventions import IdempotencyStore

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/agent/sources")
class AgentSourcesController:
    """REST adapter for the agent-tier source surface.

    Wraps the existing CQRS handlers + helpers used by
    :class:`SourcesController` without duplicating business
    logic. ``UrlFetcher`` is reused so URL-mode submissions stay
    consistent with the user-tier surface.
    """

    def __init__(
        self,
        agent_token_service: AgentTokenService,
        commands: DefaultCommandBus,
        queries: DefaultQueryBus,
        url_fetcher: UrlFetcher,
        idempotency_store: IdempotencyStore,
    ) -> None:
        self._agent_token_service = agent_token_service
        self._commands = commands
        self._queries = queries
        self._url_fetcher = url_fetcher
        self._idempotency_store = idempotency_store

    @post_mapping("", status_code=201)
    async def submit(
        self,
        http_request: Request,
        payload: Valid[Body[SubmitSourceJsonPayload]],
    ) -> SourceRecord:
        """Submit a source for intake (agent tier).

        Scope: ``agent.sources:ingest``. Mandatory
        ``Idempotency-Key`` header -- missing key returns
        ``400 missing_idempotency_key``. Always synchronous: the
        agent surface deliberately omits the user-tier
        ``mode=async`` / ``callback_url`` toggles to keep the
        contract minimal. Wire shape and 4xx mapping mirror the
        user-tier ``POST /api/v1/sources`` byte-for-byte.

        A replayed POST (same ``Idempotency-Key`` + same tenant
        within the store TTL) short-circuits the command bus and
        returns the cached :class:`SourceRecord` re-hydrated from
        the stored JSON body. The wire response is identical to
        the original ``201`` so flyradar's canon handoff can retry
        safely without creating duplicate sources.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.sources:ingest",
        )
        scope = "agent.sources:ingest"
        cached = await check_idempotency_replay(http_request, self._idempotency_store, scope)
        if cached is not None:
            # Re-hydrate the stored JSON dict back into a
            # :class:`SourceRecord` so the controller still emits
            # the typed response declared by ``post_mapping``.
            # ``cast`` narrows ``cached.body`` -- the replay write
            # always persists a ``dict`` for this route (single
            # record, not a list).
            return SourceRecord.model_validate(cached.body)
        content, content_type, filename = await self._resolve_payload_content(payload)
        record = await self._commands.send(
            SubmitSourceCommand(
                content=content,
                metadata=payload.metadata,
                filename=filename,
                content_type=content_type,
                kind=payload.kind,
                uri=payload.uri,
                actor=ctx.actor,
                correlation_id=get_correlation_id(),
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        await store_idempotent_response(
            http_request,
            self._idempotency_store,
            scope,
            status=201,
            response=record,
        )
        return record

    @get_mapping("/{source_id}")
    async def get_source(
        self,
        http_request: Request,
        source_id: PathVar[str],
    ) -> SourceRecord:
        """Read a single source record (agent tier).

        Scope: ``agent.sources:read``. Unknown ``source_id`` (or
        any id outside the verified ctx's workspace) returns
        ``404 resource_not_found`` -- the agent gate validates the
        token can speak for ``(tenant, workspace)``, and the
        query handler's scope filter completes the chain so a
        token with a multi-workspace allowlist cannot read sources
        from a workspace other than the one in the header.
        """
        ctx = await verify_agent_token(
            http_request,
            service=self._agent_token_service,
            scope="agent.sources:read",
        )
        record = await self._queries.query(
            GetSourceQuery(
                source_id=source_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        )
        if record is None:
            raise ResourceNotFoundException(f"source {source_id!r} not found")
        return record

    async def _resolve_payload_content(
        self,
        payload: SubmitSourceJsonPayload,
    ) -> tuple[bytes, str | None, str | None]:
        """Return ``(content_bytes, content_type, filename)`` for a payload.

        Mirrors :meth:`SourcesController._resolve_payload_content`
        byte-for-byte; reproduced inline so the agent controller
        doesn't reach into the user-tier controller for internal
        helpers (the user-tier method is named with a leading
        underscore for that reason). Two modes:

        * ``content_base64`` provided -- decode + return.
        * ``uri`` provided -- fetch via :class:`UrlFetcher` with
          the same size + scheme + timeout caps.
        """
        if payload.content_base64:
            try:
                return (
                    base64.b64decode(payload.content_base64),
                    payload.content_type,
                    payload.filename,
                )
            except Exception as exc:
                raise ValueError(f"content_base64 is not valid base64: {exc}") from exc

        if payload.uri:
            fetched = await self._url_fetcher.fetch(payload.uri)
            url_filename = payload.filename
            if not url_filename:
                from urllib.parse import unquote, urlparse

                terminal = urlparse(fetched.final_url).path.rsplit("/", 1)[-1]
                if terminal:
                    url_filename = unquote(terminal)
            return (
                fetched.content,
                payload.content_type or fetched.content_type,
                url_filename,
            )

        raise ValueError("either content_base64 (inline bytes) or uri (URL to fetch) is required")


__all__ = ["AgentSourcesController"]
