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

"""Outbound-URL host policy -- the server-side request forgery guard.

Two request fields make flycanon dial a caller-chosen address:
``uri`` on ``POST /api/v1/sources`` (the service fetches the document)
and ``callback_url`` on the async variant (the service POSTs the
outcome). Until 26.7.1 both accepted any ``http``/``https`` URL, which
in a multi-tenant deployment means any tenant can point the service
at ``http://127.0.0.1:8500/...`` (its own admin surface),
``http://169.254.169.254/`` (the cloud instance-metadata endpoint that
hands out IAM credentials), or any RFC 1918 host on the operator's
network, and read the response back through the ingested source.

:class:`HostPolicy` resolves the hostname **before** the connection is
made and refuses every address that is not globally routable
(loopback, private, link-local, multicast, reserved, unspecified,
shared address space, site-local, documentation and benchmarking
blocks -- the decision is ``ipaddress``'s ``is_global``). IP literals are
checked directly, ``localhost`` and single-label names are refused
outright, and a hostname whose resolution yields *any* forbidden
address is refused (an attacker who controls DNS can return a mix).
:class:`UrlFetcher` re-runs the check on every redirect hop, since a
public origin that 302s to a private address is the classic bypass.

What this does not cover, stated so nobody assumes otherwise: DNS
rebinding. The policy resolves the name, and httpx resolves it again
when it connects; a name whose answer changes between the two lookups
(a sub-second TTL and a hostile resolver) can slip through. Closing
that needs the connection pinned to the vetted address, which the
HTTP client does not expose today. The guard therefore stops the
common and cheap attacks and is documented as such in
``docs/security-model.md``.

``FLYCANON_URL_FETCH_ALLOW_PRIVATE=true`` disables the denylist for
the one legitimate case -- a dev or test stack whose origins and
webhook receivers share a private network with flycanon -- and is
never appropriate on a host tenants can reach.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

#: Resolver signature: hostname -> list of IP strings. Injectable so the
#: unit tests never touch real DNS.
Resolver = Callable[[str], list[str]]

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_LOCAL_NAMES: frozenset[str] = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
#: RFC 6598 shared address space. Named only for the error message --
#: ``is_global`` already refuses it (see :func:`address_is_forbidden`).
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


class ForbiddenHost(Exception):
    """The URL targets an address the host policy refuses."""

    code = "url_fetch_forbidden_host"

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url!r} is not an allowed outbound target: {reason}")
        self.url = url
        self.reason = reason


class UnsupportedScheme(Exception):
    """The URL scheme is not ``http`` or ``https``."""

    code = "url_fetch_unsupported_scheme"

    def __init__(self, scheme: str) -> None:
        super().__init__(f"only http/https URLs are accepted; got {scheme!r}")
        self.scheme = scheme


def default_resolver(hostname: str) -> list[str]:
    """Resolve ``hostname`` to every A/AAAA answer via the system resolver."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ForbiddenHost(hostname, f"hostname does not resolve ({exc})") from exc
    return sorted({str(info[4][0]) for info in infos})


def address_is_forbidden(ip_text: str) -> str | None:
    """Return why ``ip_text`` is refused, or ``None`` when it is routable public space.

    The blocks are taken from :mod:`ipaddress` so the list tracks the
    IANA special-purpose registry the stdlib maintains rather than a
    hand-typed table that rots: loopback (127/8, ::1), private
    (RFC 1918, fc00::/7), link-local (169.254/16 -- the cloud metadata
    range -- and fe80::/10), multicast, reserved, unspecified
    (0.0.0.0, ::), the deprecated IPv6 site-local block (fec0::/10)
    and IPv4-mapped IPv6 forms of any of those.

    The named checks exist for the error message; the decision is the
    last line, ``not addr.is_global``. The two are not the same set and
    the difference was a live hole in 26.7.1's first cut: RFC 6598
    shared address space (100.64.0.0/10 -- CGNAT, Tailscale, the pod
    CIDR of most managed Kubernetes clusters) is neither ``is_private``
    nor ``is_loopback`` nor ``is_link_local`` in :mod:`ipaddress`, yet it
    is not globally routable either, so a tenant could POST
    ``uri=http://100.64.0.1/`` and read a pod on the operator's cluster
    back through the ingested source. The same goes for the
    benchmarking and documentation ranges. ``is_global`` is the stdlib's
    own "globally reachable" column of the IANA registry, so it is the
    catch-all: anything that reaches this function and is not global is
    refused with a generic reason, and adding a new named check only
    ever improves the message, never the decision.
    """
    try:
        addr: Any = ipaddress.ip_address(ip_text)
    except ValueError:
        return f"{ip_text!r} is not a valid IP address"
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_loopback:
        return "loopback address"
    if addr.is_link_local:
        return "link-local address (instance metadata range)"
    if addr.is_private:
        return "private-network address"
    if addr.is_multicast:
        return "multicast address"
    if addr.is_reserved:
        return "reserved address"
    if addr.is_unspecified:
        return "unspecified address"
    if getattr(addr, "is_site_local", False):
        # fec0::/10 is deprecated (RFC 3879) and ``ipaddress`` reports it
        # as global, but a stack that still honours it routes it inside
        # the site -- the neighbours a tenant must not reach.
        return "site-local address"
    if addr.version == 4 and addr in _SHARED_ADDRESS_SPACE:
        return "shared address space (RFC 6598, carrier-grade NAT / cluster pod range)"
    if not addr.is_global:
        return "non-public address (IANA special-purpose block)"
    return None


class HostPolicy:
    """Decide whether an outbound URL may be dialled.

    ``allow_private=True`` turns the denylist off (scheme and syntax
    checks still apply). ``resolver`` defaults to the system resolver
    and is replaced in tests.
    """

    def __init__(self, *, allow_private: bool = False, resolver: Resolver | None = None) -> None:
        self._allow_private = allow_private
        self._resolver = resolver or default_resolver

    @property
    def allow_private(self) -> bool:
        return self._allow_private

    def check_sync(self, url: str) -> None:
        """Validate ``url`` or raise :class:`UnsupportedScheme` / :class:`ForbiddenHost`.

        Blocking (it may call DNS); use :meth:`check` from async code.
        """
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise UnsupportedScheme(scheme)
        hostname = parsed.hostname
        if not hostname:
            raise ForbiddenHost(url, "URL has no host")
        if self._allow_private:
            return
        lowered = hostname.lower().rstrip(".")
        if lowered in _LOCAL_NAMES or lowered.endswith(".localhost"):
            raise ForbiddenHost(url, "localhost is not an allowed target")
        if "." not in lowered and not _looks_like_ip(lowered):
            # A bare label (``intranet``, ``postgres``, ``valkey``) only
            # resolves through the local search domain or the container
            # network -- exactly the neighbours a tenant must not reach.
            raise ForbiddenHost(url, "single-label hostnames are not allowed")
        addresses = [lowered] if _looks_like_ip(lowered) else self._resolver(lowered)
        if not addresses:
            raise ForbiddenHost(url, "hostname does not resolve")
        for ip_text in addresses:
            reason = address_is_forbidden(ip_text)
            if reason is not None:
                raise ForbiddenHost(url, f"{ip_text} is a {reason}")

    async def check(self, url: str) -> None:
        """Async wrapper: the resolver call runs on a worker thread."""
        await asyncio.to_thread(self.check_sync, url)


def _looks_like_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


__all__ = [
    "ForbiddenHost",
    "HostPolicy",
    "Resolver",
    "UnsupportedScheme",
    "address_is_forbidden",
    "default_resolver",
]
