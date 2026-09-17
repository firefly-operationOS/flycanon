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

"""HTTP(S) URL fetcher for the URL-fetched-source intake path.

``POST /api/v1/sources`` accepts a ``uri`` field in the request --
when ``content_base64`` is omitted, the handler delegates here to
fetch the bytes before running the normal intake pipeline. We use
``httpx`` (already a transitive dep through the SDK) so the helper
inherits content-encoding negotiation and the async pool.

Safety
======

* **Host policy.** Every URL -- the one submitted and every redirect
  target -- passes :class:`HostPolicy` before a socket is opened:
  ``http``/``https`` only, and no loopback, private, link-local
  (instance-metadata), multicast, reserved or unspecified address,
  whether given as a literal or reached through DNS. Refusals raise
  ``url_fetch_forbidden_host``. See ``url_policy.py`` for the
  reasoning and the one documented gap (DNS rebinding).
* **Redirects are followed by hand.** httpx's ``follow_redirects``
  would resolve and dial each hop internally, out of the policy's
  sight, so the fetcher walks the chain itself (at most
  :data:`_MAX_REDIRECTS` hops) and re-checks each ``Location``.
* **Size cap.** HEAD probes ``Content-Length`` first; if the
  server reports more bytes than ``FLYCANON_MAX_BYTES`` we fail
  fast (no GET) with ``url_fetch_too_large``. Servers that don't
  return Content-Length fall through to a streaming GET that
  stops + raises if the cumulative read exceeds the cap.
* **Timeout.** Configurable via ``FLYCANON_URL_FETCH_TIMEOUT_S``
  (default 60s) so a slow origin can't pin a worker indefinitely.

We don't honor ``robots.txt`` -- the canonical store ingests
**caller-owned** documents; we treat the URI as an authorised
fetch target.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from pyfly.container import service

from flycanon.config import CanonSettings
from flycanon.core.services.sources.url_policy import (
    ForbiddenHost,
    HostPolicy,
    Resolver,
    UnsupportedScheme,
)

logger = logging.getLogger(__name__)

#: Redirect hops followed before the fetch is abandoned. Five matches
#: browsers' historical default and is more than any legitimate
#: document host needs.
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


class UrlFetchError(Exception):
    """Raised when the URL fetch fails (policy, size cap, network, status)."""

    code: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True, frozen=True)
class FetchedBytes:
    """Bytes + per-fetch metadata the intake pipeline carries forward."""

    content: bytes
    content_type: str | None
    content_length: int
    final_url: str


@service
class UrlFetcher:
    """httpx-backed fetcher with host policy, size, scheme and timeout caps."""

    def __init__(self, settings: CanonSettings, *, resolver: Resolver | None = None) -> None:
        self._settings = settings
        self._policy = HostPolicy(
            allow_private=bool(getattr(settings, "url_fetch_allow_private", False)),
            resolver=resolver,
        )

    @property
    def policy(self) -> HostPolicy:
        """The host policy in force -- shared with the callback-URL check."""
        return self._policy

    async def check_url(self, url: str) -> None:
        """Run the host policy on ``url`` and translate refusals to :class:`UrlFetchError`."""
        try:
            await self._policy.check(url)
        except UnsupportedScheme as exc:
            raise UrlFetchError(exc.code, str(exc)) from exc
        except ForbiddenHost as exc:
            raise UrlFetchError(exc.code, str(exc)) from exc

    async def fetch(self, uri: str) -> FetchedBytes:
        """Fetch the bytes at ``uri`` or raise :class:`UrlFetchError`.

        The flow:

        1. Run the host policy (scheme allowlist + address denylist).
        2. ``HEAD`` first to learn ``Content-Length`` +
           ``Content-Type`` (best-effort -- some servers reject
           HEAD; on 4xx/5xx we skip to step 3 and rely on the
           streaming size guard).
        3. ``GET`` streaming the body, accumulating until we hit
           the cap or the stream ends. A redirect response restarts
           at step 1 for the new location, up to ``_MAX_REDIRECTS``.
        """
        await self.check_url(uri)

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover -- transitive dep
            raise UrlFetchError(
                "url_fetch_unavailable",
                f"httpx is not installed: {exc}",
            ) from exc

        max_bytes = getattr(self._settings, "max_bytes", None) or 256 * 1024 * 1024
        timeout_s = getattr(self._settings, "url_fetch_timeout_s", 60.0)

        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0)),
        ) as client:
            # HEAD is advisory -- we still enforce the cap on the
            # GET below. We swallow HEAD failures (some CDNs are
            # GET-only) since the streaming size guard is
            # authoritative.
            try:
                head = await client.head(uri)
                declared = head.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise UrlFetchError(
                        "url_fetch_too_large",
                        f"origin reports {declared} bytes -- exceeds cap {max_bytes}",
                    )
            except httpx.HTTPError:
                logger.debug("HEAD %s failed; falling back to streaming GET", uri)

            current = uri
            for _hop in range(_MAX_REDIRECTS + 1):
                next_url = await self._get_once(client, current, max_bytes=max_bytes)
                if isinstance(next_url, FetchedBytes):
                    return next_url
                # A redirect: vet the new target with the same policy
                # before the next iteration dials it. This is the
                # whole reason redirects are followed by hand.
                logger.debug("redirect %s -> %s", current, next_url)
                current = next_url
                await self.check_url(current)
            raise UrlFetchError(
                "url_fetch_too_many_redirects",
                f"more than {_MAX_REDIRECTS} redirects fetching {uri}",
            )

    @staticmethod
    async def _get_once(client: Any, url: str, *, max_bytes: int) -> FetchedBytes | str:
        """One streaming GET: the body on success, the redirect target on 3xx."""
        try:
            async with client.stream("GET", url) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise UrlFetchError(
                            "url_fetch_http_error",
                            f"{response.status_code} from {url} without a Location header",
                        )
                    return urljoin(url, location)
                if response.status_code >= 400:
                    raise UrlFetchError(
                        "url_fetch_http_error",
                        f"{response.status_code} fetching {url}",
                    )
                chunks: list[bytes] = []
                total = 0
                async for piece in response.aiter_bytes(chunk_size=64 * 1024):
                    total += len(piece)
                    if total > max_bytes:
                        raise UrlFetchError(
                            "url_fetch_too_large",
                            f"stream exceeded cap {max_bytes}",
                        )
                    chunks.append(piece)
                return FetchedBytes(
                    content=b"".join(chunks),
                    content_type=response.headers.get("Content-Type"),
                    content_length=total,
                    final_url=str(response.url),
                )
        except UrlFetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UrlFetchError(
                "url_fetch_failed",
                f"GET {url} failed: {exc}",
            ) from exc
