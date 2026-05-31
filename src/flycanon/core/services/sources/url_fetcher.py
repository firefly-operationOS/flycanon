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
inherits redirect handling, content-encoding negotiation, and the
async pool.

Safety
======

* **Size cap.** HEAD probes ``Content-Length`` first; if the
  server reports more bytes than ``FLYCANON_MAX_BYTES`` we fail
  fast (no GET) with ``url_fetch_too_large``. Servers that don't
  return Content-Length fall through to a streaming GET that
  stops + raises if the cumulative read exceeds the cap.
* **Scheme allowlist.** Only ``http`` / ``https``. ``file:``,
  ``ftp:``, ``data:``, ``javascript:`` are rejected --
  unauthenticated server-side request forgery is a classic
  attack vector and the canonical store is no place for it.
* **Timeout.** Configurable via ``FLYCANON_URL_FETCH_TIMEOUT_S``
  (default 60s) so a slow origin can't pin a worker indefinitely.

We don't honor ``robots.txt`` -- the canonical store ingests
**caller-owned** documents; we treat the URI as an authorised
fetch target.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from pyfly.container import service

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)


_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class UrlFetchError(Exception):
    """Raised when the URL fetch fails (size cap, network, status)."""

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
    """Tiny httpx-backed fetcher with size + scheme + timeout caps."""

    def __init__(self, settings: CanonSettings) -> None:
        self._settings = settings

    async def fetch(self, uri: str) -> FetchedBytes:
        """Fetch the bytes at ``uri`` or raise :class:`UrlFetchError`.

        The flow:

        1. Validate the scheme. Anything outside ``http`` /
           ``https`` raises ``url_fetch_unsupported_scheme``.
        2. ``HEAD`` first to learn ``Content-Length`` +
           ``Content-Type`` (best-effort -- some servers reject
           HEAD; on 4xx/5xx we skip to step 3 and rely on the
           streaming size guard).
        3. ``GET`` streaming the body, accumulating until we hit
           the cap or the stream ends.
        """
        scheme = urlparse(uri).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise UrlFetchError(
                "url_fetch_unsupported_scheme",
                f"only http/https URLs are accepted; got {scheme!r}",
            )

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
            follow_redirects=True,
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

            try:
                async with client.stream("GET", uri) as response:
                    if response.status_code >= 400:
                        raise UrlFetchError(
                            "url_fetch_http_error",
                            f"{response.status_code} fetching {uri}",
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
                    content = b"".join(chunks)
                    return FetchedBytes(
                        content=content,
                        content_type=response.headers.get("Content-Type"),
                        content_length=total,
                        final_url=str(response.url),
                    )
            except UrlFetchError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise UrlFetchError(
                    "url_fetch_failed",
                    f"GET {uri} failed: {exc}",
                ) from exc
