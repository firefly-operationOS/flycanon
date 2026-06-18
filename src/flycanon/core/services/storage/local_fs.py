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

"""Local-filesystem ObjectStore backend (dev / test default)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from flycanon.core.services.storage.object_store import ObjectStore


class LocalFsObjectStore(ObjectStore):
    """Store objects as files under a configurable root directory.

    ``root`` is the bucket equivalent; keys are joined onto it. Blocking file
    I/O runs on a worker thread via ``asyncio.to_thread`` so the event loop is
    never blocked, matching flycanon's async-first adapters without pulling in
    an extra aiofiles dependency.
    """

    def __init__(self, root: str) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if ".." in key.split("/") or key.startswith("/"):
            raise ValueError(f"illegal key {key!r}")
        # ``Path(root) / key`` discards the root when ``key`` is absolute, so
        # the leading-slash check above is the real guard; resolve and confirm
        # the result is still inside the root as defence in depth.
        path = (self._root / key).resolve()
        if path != self._root and self._root not in path.parents:
            raise ValueError(f"illegal key {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        # content_type is not persisted on the local filesystem; it is part of
        # the port so the S3 backend can set object metadata.
        path = self._path(key)
        await asyncio.to_thread(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        return await asyncio.to_thread(self._read, path, key)

    def get_sync(self, key: str) -> bytes:
        # Blocking read for callers already on a worker thread (the RLM REPL).
        return self._read(self._path(key), key)

    @staticmethod
    def _read(path: Path, key: str) -> bytes:
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)

    async def exists(self, key: str) -> bool:
        path = self._path(key)
        return await asyncio.to_thread(path.is_file)
