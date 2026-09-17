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

"""Coverage for :class:`UrlFetcher`.

We use ``respx`` (already a dev dep, used by the SDK tests) to mock
the network. The fetcher must enforce:

* Scheme allowlist (http/https only).
* The host policy (no loopback / private / link-local targets), on
  the submitted URL and on every redirect hop.
* Streaming size cap.
* Bubble up 4xx/5xx as ``url_fetch_http_error``.
* HEAD probe is advisory -- a HEAD-rejecting server still works
  via the GET path's size guard.

DNS is stubbed through the injectable resolver so ``example.com``
resolves to a public address without touching the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from flycanon.core.services.sources.url_fetcher import UrlFetcher, UrlFetchError

_PUBLIC_IP = "93.184.216.34"


def _settings(*, max_bytes: int = 1024, timeout_s: float = 5.0, allow_private: bool = False):
    return SimpleNamespace(
        max_bytes=max_bytes,
        url_fetch_timeout_s=timeout_s,
        url_fetch_allow_private=allow_private,
    )


def _public_resolver(host: str) -> list[str]:
    """Every ``*.example.com`` name is public; ``internal.example.com`` is RFC 1918."""
    if host == "internal.example.com":
        return ["10.0.0.7"]
    return [_PUBLIC_IP]


def _fetcher(settings=None) -> UrlFetcher:
    return UrlFetcher(settings or _settings(), resolver=_public_resolver)


class TestSchemeAllowlist:
    @pytest.mark.asyncio
    async def test_file_scheme_rejected(self):
        fetcher = _fetcher()
        with pytest.raises(UrlFetchError) as exc_info:
            await fetcher.fetch("file:///etc/passwd")
        assert exc_info.value.code == "url_fetch_unsupported_scheme"

    @pytest.mark.asyncio
    async def test_data_scheme_rejected(self):
        fetcher = _fetcher()
        with pytest.raises(UrlFetchError) as exc_info:
            await fetcher.fetch("data:text/plain;base64,SGVsbG8=")
        assert exc_info.value.code == "url_fetch_unsupported_scheme"


class TestStreamingFetch:
    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_happy_path_returns_bytes_plus_metadata(self, respx_mock):
        respx_mock.head("https://example.com/a.pdf").mock(
            return_value=httpx.Response(200, headers={"Content-Length": "5"})
        )
        respx_mock.get("https://example.com/a.pdf").mock(
            return_value=httpx.Response(
                200,
                content=b"hello",
                headers={"Content-Type": "application/pdf"},
            )
        )
        fetched = await _fetcher().fetch("https://example.com/a.pdf")
        assert fetched.content == b"hello"
        assert fetched.content_type == "application/pdf"
        assert fetched.content_length == 5

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_head_too_large_rejected_before_get(self, respx_mock):
        respx_mock.head("https://example.com/big").mock(
            return_value=httpx.Response(200, headers={"Content-Length": "100000000"})
        )
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher(_settings(max_bytes=1024)).fetch("https://example.com/big")
        assert exc_info.value.code == "url_fetch_too_large"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_stream_exceeding_cap_aborts(self, respx_mock):
        # HEAD doesn't report length (some CDNs); the streaming GET
        # must enforce the cap by aborting once total > cap.
        respx_mock.head("https://example.com/big").mock(return_value=httpx.Response(200, headers={}))
        respx_mock.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"x" * 5000))
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher(_settings(max_bytes=1024)).fetch("https://example.com/big")
        assert exc_info.value.code == "url_fetch_too_large"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_4xx_is_url_fetch_http_error(self, respx_mock):
        respx_mock.head("https://example.com/x").mock(
            return_value=httpx.Response(200, headers={"Content-Length": "10"})
        )
        respx_mock.get("https://example.com/x").mock(return_value=httpx.Response(404))
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher().fetch("https://example.com/x")
        assert exc_info.value.code == "url_fetch_http_error"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_head_failure_falls_through_to_get(self, respx_mock):
        # Some origins reject HEAD with 405 -- the fetcher should
        # fall through to the streaming GET (the size cap is still
        # enforced there).
        respx_mock.head("https://example.com/y").mock(return_value=httpx.Response(405))
        respx_mock.get("https://example.com/y").mock(
            return_value=httpx.Response(
                200,
                content=b"ok",
                headers={"Content-Type": "text/plain"},
            )
        )
        fetched = await _fetcher().fetch("https://example.com/y")
        assert fetched.content == b"ok"
        assert fetched.content_type == "text/plain"


class TestHostPolicyOnFetch:
    """The SSRF guard as seen from the fetcher (26.7.1)."""

    @pytest.mark.asyncio
    async def test_private_literal_refused_before_any_request(self):
        with respx.mock(assert_all_called=False) as router:
            route = router.get("http://127.0.0.1:8500/api/v1/version").mock(return_value=httpx.Response(200))
            with pytest.raises(UrlFetchError) as exc_info:
                await _fetcher().fetch("http://127.0.0.1:8500/api/v1/version")
            assert exc_info.value.code == "url_fetch_forbidden_host"
            assert not route.called

    @pytest.mark.asyncio
    async def test_metadata_endpoint_refused(self):
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher().fetch("http://169.254.169.254/latest/meta-data/iam/")
        assert exc_info.value.code == "url_fetch_forbidden_host"

    @pytest.mark.asyncio
    async def test_hostname_resolving_to_private_refused(self):
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher().fetch("https://internal.example.com/secret.pdf")
        assert exc_info.value.code == "url_fetch_forbidden_host"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_redirect_to_private_host_is_refused(self, respx_mock):
        """A public origin that 302s to a private address is the classic bypass."""
        respx_mock.head("https://example.com/doc").mock(return_value=httpx.Response(200))
        respx_mock.get("https://example.com/doc").mock(
            return_value=httpx.Response(302, headers={"Location": "http://10.0.0.7/internal.pdf"})
        )
        private = respx_mock.get("http://10.0.0.7/internal.pdf").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher().fetch("https://example.com/doc")
        assert exc_info.value.code == "url_fetch_forbidden_host"
        assert not private.called

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_redirect_to_public_host_is_followed(self, respx_mock):
        respx_mock.head("https://example.com/doc").mock(return_value=httpx.Response(200))
        respx_mock.get("https://example.com/doc").mock(
            return_value=httpx.Response(301, headers={"Location": "/moved/doc.pdf"})
        )
        respx_mock.get("https://example.com/moved/doc.pdf").mock(
            return_value=httpx.Response(200, content=b"moved", headers={"Content-Type": "application/pdf"})
        )
        fetched = await _fetcher().fetch("https://example.com/doc")
        assert fetched.content == b"moved"
        assert fetched.final_url == "https://example.com/moved/doc.pdf"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_redirect_loop_is_capped(self, respx_mock):
        respx_mock.head("https://example.com/loop").mock(return_value=httpx.Response(200))
        respx_mock.get("https://example.com/loop").mock(
            return_value=httpx.Response(302, headers={"Location": "https://example.com/loop"})
        )
        with pytest.raises(UrlFetchError) as exc_info:
            await _fetcher().fetch("https://example.com/loop")
        assert exc_info.value.code == "url_fetch_too_many_redirects"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_allow_private_lets_a_dev_stack_fetch_its_neighbours(self, respx_mock):
        respx_mock.head("http://cp-web:8080/blob").mock(return_value=httpx.Response(200))
        respx_mock.get("http://cp-web:8080/blob").mock(return_value=httpx.Response(200, content=b"ok"))
        fetched = await _fetcher(_settings(allow_private=True)).fetch("http://cp-web:8080/blob")
        assert fetched.content == b"ok"

    @pytest.mark.asyncio
    async def test_check_url_exposes_the_policy_for_callback_urls(self):
        fetcher = _fetcher()
        await fetcher.check_url("https://hooks.example.com/flycanon")
        with pytest.raises(UrlFetchError) as exc_info:
            await fetcher.check_url("http://192.168.1.20/hook")
        assert exc_info.value.code == "url_fetch_forbidden_host"
