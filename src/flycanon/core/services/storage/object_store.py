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

"""Hexagonal ObjectStore port for persisting original documents.

The port is deliberately narrow -- ``put``/``get``/``delete``/``exists`` over
opaque ``bytes`` -- because the only thing canon stores here is the raw
document an intake submitted, keyed by ``tenant/workspace/.../files/{id}.{ext}``
(see the RLM integration design spec). Adapters: ``LocalFsObjectStore`` (dev /
test, default) and ``S3ObjectStore`` (prod, needs the ``s3`` extra). Mirrors
flyquery's object-store layout so the two services share key conventions.

Wiring into ``IntakeService`` lands in a later PR; this module only defines the
port and its backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ObjectStore(ABC):
    """Blob storage port: put/get/delete/exists over opaque bytes.

    Keys are forward-slash-delimited paths relative to the backend root (the
    local base directory or the ``s3://bucket/prefix``). Implementations reject
    ``..`` segments and absolute keys so a caller-supplied key cannot escape
    that root.
    """

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Write ``data`` at ``key``, overwriting any existing object."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Return the bytes at ``key``; raise ``FileNotFoundError`` if absent."""

    @abstractmethod
    def get_sync(self, key: str) -> bytes:
        """Blocking variant of :meth:`get` for callers already on a worker thread.

        The RLM REPL runs synchronously inside ``asyncio.to_thread``, so it
        cannot await :meth:`get`; the lazy corpus fetches originals through this
        method instead. Same key guards and ``FileNotFoundError`` contract as
        :meth:`get`.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove ``key``; a no-op if it does not exist."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return whether an object is stored at ``key``."""
