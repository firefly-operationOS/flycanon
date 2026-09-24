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

"""HMAC signing of outbound webhooks (``X-Flycanon-Signature``).

Until 26.7.1 the async-ingest callback was a bare ``POST`` with a JSON
body: a receiver had no way to know the delivery came from flycanon
rather than from anyone who learned the callback URL, and no defence
against a captured delivery being replayed later. This module is the
signing half of the fix; the verifying half is documented for
receivers in ``docs/async-ingest.md`` and mirrored here as
:func:`verify_signature` so a Python receiver (and our tests) can call
the exact algorithm.

Header format (Stripe-style, so receivers can reuse existing tooling)::

    X-Flycanon-Signature: t=1726570000,v1=<hex sha256>

where ``v1`` is ``HMAC-SHA256(secret, f"{t}.{raw_body}")`` over the
**raw request body bytes exactly as sent** and ``t`` is the Unix
timestamp (seconds) at which the signature was produced. Binding the
timestamp into the MAC is what lets a receiver reject stale replays:
:func:`verify_signature` refuses anything older than ``tolerance_s``.

The body is signed as bytes, not as a parsed object, because JSON has
no canonical form -- two libraries can serialise the same dict with
different key order or spacing and the MACs would disagree. Callers
therefore serialise once, sign those bytes, and send those same bytes
(see :class:`AsyncIngestService._fire_webhook`).

When ``FLYCANON_WEBHOOK_SECRET`` is empty no header is emitted and
:func:`log_webhook_signing_mode` says so at boot. Unsigned delivery is
kept as a mode (rather than refusing to fire) so an existing receiver
does not silently stop getting callbacks on upgrade; the boot warning
is how the operator learns to close the gap.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from flycanon.config import CanonSettings

logger = logging.getLogger(__name__)

SIGNATURE_VERSION = "v1"


def sign_payload(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    """Return the ``t=<ts>,v1=<hex>`` header value for ``body``.

    ``timestamp`` is injectable for deterministic tests; production
    callers leave it ``None`` and get ``time.time()``.
    """
    if not secret:
        raise ValueError("webhook secret must be non-empty to sign a payload")
    ts = int(time.time()) if timestamp is None else int(timestamp)
    digest = _mac(secret, ts, body)
    return f"t={ts},{SIGNATURE_VERSION}={digest}"


def verify_signature(
    secret: str,
    body: bytes,
    header: str | None,
    *,
    tolerance_s: int = 300,
    now: float | None = None,
) -> bool:
    """Return ``True`` when ``header`` is a valid, fresh signature of ``body``.

    Fresh means ``|now - t| <= tolerance_s``; the default five minutes
    matches the tolerance Stripe and GitHub document, wide enough for
    clock skew and narrow enough that a captured delivery is useless
    after lunch. Comparison of the MAC is constant-time.
    """
    if not secret or not header:
        return False
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    ts_text = parts.get("t")
    provided = parts.get(SIGNATURE_VERSION)
    if ts_text is None or provided is None:
        return False
    try:
        ts = int(ts_text)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance_s:
        return False
    expected = _mac(secret, ts, body)
    return hmac.compare_digest(expected, provided)


def _mac(secret: str, ts: int, body: bytes) -> str:
    message = f"{ts}.".encode() + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def log_webhook_signing_mode(settings: CanonSettings) -> None:
    """Announce at boot whether callbacks carry ``X-Flycanon-Signature``."""
    if settings.webhook_secret:
        logger.info(
            "webhook signing ENABLED: async-ingest callbacks carry X-Flycanon-Signature (HMAC-SHA256)"
        )
        return
    logger.warning(
        "webhook signing DISABLED: FLYCANON_WEBHOOK_SECRET is empty, so async-ingest "
        "callbacks are delivered unsigned and a receiver cannot authenticate them. "
        "Set FLYCANON_WEBHOOK_SECRET before relying on callback_url in production."
    )


__all__ = ["SIGNATURE_VERSION", "log_webhook_signing_mode", "sign_payload", "verify_signature"]
