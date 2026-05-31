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

"""Actor resolver.

The conventions module does NOT verify JWT signatures — that
belongs to the API gateway / IDP integration. Here we just *read*
the claims from an already-decoded payload and produce the
canonical ``actor`` string.
"""

from __future__ import annotations

from flycanon.web.conventions.actor import (
    Actor,
    actor_from_agent_token,
    actor_from_jwt_claims,
    decode_jwt_unverified,
)


def test_actor_from_jwt_uses_sub() -> None:
    actor = actor_from_jwt_claims({"sub": "alice@example.com", "tenant": "acme"})
    assert actor == Actor(value="user:alice@example.com", tenant_claim="acme")


def test_actor_from_jwt_without_tenant_claim() -> None:
    actor = actor_from_jwt_claims({"sub": "alice"})
    assert actor == Actor(value="user:alice", tenant_claim=None)


def test_actor_from_jwt_rejects_empty_sub() -> None:
    actor = actor_from_jwt_claims({"sub": ""})
    assert actor is None


def test_actor_from_agent_token_prefix() -> None:
    actor = actor_from_agent_token("agt_abc12345_secret")
    assert actor == Actor(value="agent:agt_abc12345", tenant_claim=None)


def test_actor_from_agent_token_too_short() -> None:
    # Token shorter than the prefix length is invalid.
    actor = actor_from_agent_token("agt_x")
    assert actor is None


def test_decode_jwt_returns_none_for_non_bearer_scheme() -> None:
    assert decode_jwt_unverified("Basic abc") is None
    assert decode_jwt_unverified("notbearer abc.def.ghi") is None


def test_decode_jwt_returns_none_for_missing_payload_segment() -> None:
    assert decode_jwt_unverified("Bearer single") is None


def test_decode_jwt_returns_none_for_non_dict_payload() -> None:
    import base64

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = b64(b'{"alg":"none","typ":"JWT"}')
    for payload in (b"null", b"[1,2,3]", b"42", b'"hello"'):
        bearer = f"Bearer {header}.{b64(payload)}."
        assert decode_jwt_unverified(bearer) is None, payload


def test_decode_jwt_returns_none_for_invalid_base64() -> None:
    # Garbage in the payload segment — base64 decode raises ValueError.
    assert decode_jwt_unverified("Bearer eyJhbGciOiJub25lIn0.!!!.") is None


def test_decode_jwt_returns_dict_for_valid_payload() -> None:
    import base64

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = b64(b'{"alg":"none"}')
    body = b64(b'{"sub":"alice"}')
    decoded = decode_jwt_unverified(f"Bearer {header}.{body}.")
    assert isinstance(decoded, dict)
    assert decoded["sub"] == "alice"
