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

"""``HostPolicy`` -- the outbound-URL denylist behind ``uri`` and ``callback_url``.

DNS is never touched: every test injects a resolver so the answers
are deterministic and CI runs without network.
"""

from __future__ import annotations

import pytest

from flycanon.core.services.sources.url_policy import (
    ForbiddenHost,
    HostPolicy,
    UnsupportedScheme,
    address_is_forbidden,
)


def _resolver(table: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        return table.get(host, [])

    return resolve


_PUBLIC = "93.184.216.34"


@pytest.mark.parametrize(
    "ip, expected_fragment",
    [
        ("127.0.0.1", "loopback"),
        ("127.8.8.8", "loopback"),
        ("::1", "loopback"),
        ("10.0.0.5", "private"),
        ("172.16.4.1", "private"),
        ("192.168.1.1", "private"),
        ("169.254.169.254", "link-local"),
        ("fe80::1", "link-local"),
        ("fd00::1", "private"),
        ("224.0.0.1", "multicast"),
        ("0.0.0.0", "address"),
        ("::ffff:127.0.0.1", "loopback"),
        ("::ffff:10.1.1.1", "private"),
        ("240.0.0.1", "address"),
    ],
)
def test_forbidden_addresses(ip: str, expected_fragment: str) -> None:
    reason = address_is_forbidden(ip)
    assert reason is not None and expected_fragment in reason


@pytest.mark.parametrize("ip", [_PUBLIC, "8.8.8.8", "2606:4700::1111"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert address_is_forbidden(ip) is None


def test_garbage_is_not_an_address() -> None:
    assert address_is_forbidden("not-an-ip") is not None


class TestHostPolicy:
    def test_public_hostname_passes(self) -> None:
        policy = HostPolicy(resolver=_resolver({"docs.example.com": [_PUBLIC]}))
        policy.check_sync("https://docs.example.com/a.pdf")

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8500/api/v1/agent-tokens",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/",
            "http://192.168.0.10:5432/",
        ],
    )
    def test_ip_literals_are_refused(self, url: str) -> None:
        policy = HostPolicy(resolver=_resolver({}))
        with pytest.raises(ForbiddenHost) as exc_info:
            policy.check_sync(url)
        assert exc_info.value.code == "url_fetch_forbidden_host"

    @pytest.mark.parametrize("url", ["http://localhost/", "http://LOCALHOST:8500/", "http://foo.localhost/"])
    def test_localhost_names_are_refused(self, url: str) -> None:
        with pytest.raises(ForbiddenHost):
            HostPolicy(resolver=_resolver({})).check_sync(url)

    @pytest.mark.parametrize("url", ["http://postgres:5432/", "http://valkey/", "http://intranet/"])
    def test_single_label_names_are_refused(self, url: str) -> None:
        # Compose-network neighbours and search-domain hosts.
        with pytest.raises(ForbiddenHost):
            HostPolicy(resolver=_resolver({})).check_sync(url)

    def test_hostname_resolving_to_private_is_refused(self) -> None:
        policy = HostPolicy(resolver=_resolver({"evil.example.com": ["10.0.0.9"]}))
        with pytest.raises(ForbiddenHost) as exc_info:
            policy.check_sync("https://evil.example.com/doc")
        assert "10.0.0.9" in str(exc_info.value)

    def test_mixed_answers_are_refused(self) -> None:
        # A hostile resolver that returns one public and one private
        # address must not get through on the public one.
        policy = HostPolicy(resolver=_resolver({"mixed.example.com": [_PUBLIC, "192.168.1.5"]}))
        with pytest.raises(ForbiddenHost):
            policy.check_sync("https://mixed.example.com/")

    def test_unresolvable_hostname_is_refused(self) -> None:
        policy = HostPolicy(resolver=_resolver({}))
        with pytest.raises(ForbiddenHost):
            policy.check_sync("https://nope.example.com/")

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.example.com/", "gopher://x.example.com/"])
    def test_non_http_schemes_are_refused(self, url: str) -> None:
        with pytest.raises(UnsupportedScheme) as exc_info:
            HostPolicy(resolver=_resolver({})).check_sync(url)
        assert exc_info.value.code == "url_fetch_unsupported_scheme"

    def test_missing_host_is_refused(self) -> None:
        with pytest.raises(ForbiddenHost):
            HostPolicy(resolver=_resolver({})).check_sync("http:///path-only")

    def test_allow_private_disables_the_denylist_only(self) -> None:
        policy = HostPolicy(allow_private=True, resolver=_resolver({}))
        policy.check_sync("http://127.0.0.1:8080/hook")
        policy.check_sync("http://cp-web:8080/hook")
        with pytest.raises(UnsupportedScheme):
            policy.check_sync("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_async_check_delegates(self) -> None:
        policy = HostPolicy(resolver=_resolver({"docs.example.com": [_PUBLIC]}))
        await policy.check("https://docs.example.com/")
        with pytest.raises(ForbiddenHost):
            await policy.check("http://10.0.0.1/")
