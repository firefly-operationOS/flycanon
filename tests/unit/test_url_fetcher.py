# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for :class:`UrlFetcher`.

We use ``respx`` (already a dev dep, used by the SDK tests) to mock
the network. The fetcher must enforce:

* Scheme allowlist (http/https only).
* Streaming size cap.
* Bubble up 4xx/5xx as ``url_fetch_http_error``.
* HEAD probe is advisory -- a HEAD-rejecting server still works
  via the GET path's size guard.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from flycanon.core.services.sources.url_fetcher import UrlFetchError, UrlFetcher


def _settings(*, max_bytes: int = 1024, timeout_s: float = 5.0):
    return SimpleNamespace(max_bytes=max_bytes, url_fetch_timeout_s=timeout_s)


class TestSchemeAllowlist:
    @pytest.mark.asyncio
    async def test_file_scheme_rejected(self):
        fetcher = UrlFetcher(_settings())
        with pytest.raises(UrlFetchError) as exc_info:
            await fetcher.fetch("file:///etc/passwd")
        assert exc_info.value.code == "url_fetch_unsupported_scheme"

    @pytest.mark.asyncio
    async def test_data_scheme_rejected(self):
        fetcher = UrlFetcher(_settings())
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
        fetched = await UrlFetcher(_settings()).fetch("https://example.com/a.pdf")
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
            await UrlFetcher(_settings(max_bytes=1024)).fetch("https://example.com/big")
        assert exc_info.value.code == "url_fetch_too_large"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_stream_exceeding_cap_aborts(self, respx_mock):
        # HEAD doesn't report length (some CDNs); the streaming GET
        # must enforce the cap by aborting once total > cap.
        respx_mock.head("https://example.com/big").mock(
            return_value=httpx.Response(200, headers={})
        )
        respx_mock.get("https://example.com/big").mock(
            return_value=httpx.Response(200, content=b"x" * 5000)
        )
        with pytest.raises(UrlFetchError) as exc_info:
            await UrlFetcher(_settings(max_bytes=1024)).fetch("https://example.com/big")
        assert exc_info.value.code == "url_fetch_too_large"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_4xx_is_url_fetch_http_error(self, respx_mock):
        respx_mock.head("https://example.com/x").mock(
            return_value=httpx.Response(200, headers={"Content-Length": "10"})
        )
        respx_mock.get("https://example.com/x").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(UrlFetchError) as exc_info:
            await UrlFetcher(_settings()).fetch("https://example.com/x")
        assert exc_info.value.code == "url_fetch_http_error"

    @pytest.mark.asyncio
    @respx.mock(assert_all_called=False)
    async def test_head_failure_falls_through_to_get(self, respx_mock):
        # Some origins reject HEAD with 405 -- the fetcher should
        # fall through to the streaming GET (the size cap is still
        # enforced there).
        respx_mock.head("https://example.com/y").mock(
            return_value=httpx.Response(405)
        )
        respx_mock.get("https://example.com/y").mock(
            return_value=httpx.Response(
                200,
                content=b"ok",
                headers={"Content-Type": "text/plain"},
            )
        )
        fetched = await UrlFetcher(_settings()).fetch("https://example.com/y")
        assert fetched.content == b"ok"
        assert fetched.content_type == "text/plain"
