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

"""S3 ObjectStore backend (prod). Requires ``uv sync --extra s3``.

boto3 is synchronous, so each call is offloaded onto a worker thread with
``asyncio.to_thread`` -- the same pattern the local backend uses -- to keep the
event loop free. AWS credentials are resolved by boto3 from the standard
environment (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / profiles /
instance roles); only the bucket, key prefix and optional endpoint/region are
configured here.
"""

from __future__ import annotations

import asyncio

from flycanon.core.services.storage.object_store import ObjectStore

# Guarded like the qdrant/chroma vector-store backends: importing this module
# without the ``s3`` extra installed must not crash; the factory raises a clear
# error only when an S3 store is actually requested.
try:
    import boto3
    from botocore.exceptions import ClientError

    _HAS_BOTO3 = True
except ImportError:  # pragma: no cover -- exercised via the s3 extra being absent
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment, misc]
    _HAS_BOTO3 = False


class S3ObjectStore(ObjectStore):
    """Bucket-relative ObjectStore backed by boto3.

    Keys are placed under ``prefix`` within ``bucket``. ``endpoint_url`` allows
    pointing at MinIO or other S3-compatible services; ``region`` is forwarded
    to the client when set.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
    ) -> None:
        if not _HAS_BOTO3:
            raise RuntimeError("S3ObjectStore requires the 's3' extra; run: uv sync --extra s3")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
        )

    def _full_key(self, key: str) -> str:
        if ".." in key.split("/") or key.startswith("/"):
            raise ValueError(f"illegal key {key!r}")
        if not self._prefix:
            return key
        return f"{self._prefix}/{key}"

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        full = self._full_key(key)
        kwargs: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": full,
            "Body": data,
        }
        if content_type is not None:
            kwargs["ContentType"] = content_type
        await asyncio.to_thread(lambda: self._client.put_object(**kwargs))

    async def get(self, key: str) -> bytes:
        full = self._full_key(key)
        try:
            resp = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=full)
        except ClientError as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(key) from exc
            raise
        return await asyncio.to_thread(resp["Body"].read)

    def get_sync(self, key: str) -> bytes:
        # Blocking read for callers already on a worker thread (the RLM REPL).
        # boto3 is synchronous, so the client call is issued directly here.
        full = self._full_key(key)
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=full)
        except ClientError as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(key) from exc
            raise
        return resp["Body"].read()

    async def delete(self, key: str) -> None:
        full = self._full_key(key)
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=full)

    async def exists(self, key: str) -> bool:
        full = self._full_key(key)
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=full)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise
        return True


def _is_not_found(exc: ClientError) -> bool:
    """Whether a boto3 ClientError represents a missing key / 404."""
    error = getattr(exc, "response", {}).get("Error", {})
    return error.get("Code") in ("404", "NoSuchKey", "NotFound")
