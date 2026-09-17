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

"""``X-Flycanon-Signature`` -- sign / verify round trip and its edges."""

from __future__ import annotations

import hashlib
import hmac
import logging

import pytest

from flycanon.config import CanonSettings
from flycanon.web.conventions.webhook_signature import (
    log_webhook_signing_mode,
    sign_payload,
    verify_signature,
)

_SECRET = "whsec_test"
_BODY = b'{"job_id":"j1","status":"succeeded"}'


def test_signature_format_and_algorithm_are_the_documented_ones() -> None:
    header = sign_payload(_SECRET, _BODY, timestamp=1726570000)
    expected = hmac.new(_SECRET.encode(), b"1726570000." + _BODY, hashlib.sha256).hexdigest()
    assert header == f"t=1726570000,v1={expected}"


def test_round_trip_verifies_within_tolerance() -> None:
    header = sign_payload(_SECRET, _BODY, timestamp=1000)
    assert verify_signature(_SECRET, _BODY, header, now=1100) is True
    assert verify_signature(_SECRET, _BODY, header, now=1000 + 300) is True


def test_stale_signature_is_rejected() -> None:
    header = sign_payload(_SECRET, _BODY, timestamp=1000)
    assert verify_signature(_SECRET, _BODY, header, now=1000 + 301) is False


def test_tampered_body_or_secret_is_rejected() -> None:
    header = sign_payload(_SECRET, _BODY, timestamp=1000)
    assert verify_signature(_SECRET, _BODY + b" ", header, now=1000) is False
    assert verify_signature("other", _BODY, header, now=1000) is False


@pytest.mark.parametrize("header", [None, "", "v1=abc", "t=notanumber,v1=abc", "t=1000", "garbage"])
def test_malformed_headers_are_rejected(header: str | None) -> None:
    assert verify_signature(_SECRET, _BODY, header, now=1000) is False


def test_empty_secret_cannot_sign_and_never_verifies() -> None:
    with pytest.raises(ValueError):
        sign_payload("", _BODY)
    assert verify_signature("", _BODY, "t=1,v1=x", now=1) is False


def test_boot_log_states_the_mode(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="flycanon.web.conventions.webhook_signature"):
        log_webhook_signing_mode(CanonSettings(webhook_secret=""))
        log_webhook_signing_mode(CanonSettings(webhook_secret="s"))
    levels = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert any(lvl == "WARNING" and "webhook signing DISABLED" in msg for lvl, msg in levels)
    assert any(lvl == "INFO" and "webhook signing ENABLED" in msg for lvl, msg in levels)
