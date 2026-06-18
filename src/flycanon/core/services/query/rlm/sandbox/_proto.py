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

"""The wire protocol shared by the sandbox parent and child.

Frames are length-prefixed JSON: a 4-byte big-endian unsigned length followed
by that many bytes of UTF-8 JSON. This is the **single source of truth** for the
framing -- both :mod:`executor` (parent) and :mod:`runner` (child) import it, so
the two sides can never drift.

Security: frames are decoded with :mod:`json` only. Neither side ever
``pickle``\\ s or ``eval``\\ s a peer's bytes, and every read is bounded by
:data:`MAX_FRAME` so a hostile peer cannot exhaust memory with a huge length
prefix.
"""

from __future__ import annotations

import json
import struct

# 4-byte big-endian unsigned length prefix.
_HEADER = struct.Struct(">I")
HEADER_LEN = _HEADER.size

# Hard cap on a single frame's JSON payload. Document text fetched by an RPC
# result can be large, so this is generous; it exists only to stop an attacker
# (or a bug) from announcing a multi-gigabyte length and exhausting memory.
MAX_FRAME = 64 * 1024 * 1024  # 64 MiB


class ProtocolError(Exception):
    """A frame could not be read or decoded -- the peer is malformed/hostile."""


def encode(frame: dict) -> bytes:
    """Serialise ``frame`` to a length-prefixed JSON message.

    Raises :class:`ProtocolError` if the encoded payload exceeds
    :data:`MAX_FRAME` (so we never put a frame on the wire the peer would be
    forced to reject).
    """
    payload = json.dumps(frame).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise ProtocolError(f"frame too large: {len(payload)} bytes")
    return _HEADER.pack(len(payload)) + payload


def _read_exact(read, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``read(size)`` or raise.

    ``read`` is any callable returning up to ``size`` bytes (``conn.recv`` or
    ``file.read``). A short read at the very start (EOF before any byte of a
    frame) raises :class:`EOFError`; a short read mid-frame raises
    :class:`ProtocolError` (truncated peer).
    """
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = read(remaining)
        if not chunk:
            if not chunks:
                raise EOFError("connection closed")
            raise ProtocolError("truncated frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def decode(read) -> dict:
    """Read one frame using the ``read(size)`` callable and return its dict.

    Raises :class:`EOFError` at a clean end of stream, or :class:`ProtocolError`
    on a truncated frame, an oversized length prefix, non-JSON bytes, or a
    top-level value that is not a JSON object.
    """
    header = _read_exact(read, HEADER_LEN)
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME:
        raise ProtocolError(f"frame too large: {length} bytes")
    payload = _read_exact(read, length)
    try:
        frame = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"undecodable frame: {exc}") from exc
    if not isinstance(frame, dict):
        raise ProtocolError("frame is not a JSON object")
    return frame
