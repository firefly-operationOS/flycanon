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

"""Unit tests for the ObjectStore port, backends, and factory.

LocalFs tests use ``tmp_path``. S3 tests never touch the network: a fake boto3
client records the calls and the backend is asserted to issue put/get/delete/
head with the right keys.
"""

from __future__ import annotations

import io

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.storage import s3 as s3_module
from flycanon.core.services.storage.factory import build_object_store
from flycanon.core.services.storage.local_fs import LocalFsObjectStore
from flycanon.core.services.storage.object_store import ObjectStore
from flycanon.core.services.storage.s3 import S3ObjectStore

# ---------------------------------------------------------------- LocalFs ----


async def test_localfs_put_get_roundtrip(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    await store.put("t/w/files/a.txt", b"hello", content_type="text/plain")
    assert await store.get("t/w/files/a.txt") == b"hello"


async def test_localfs_writes_under_root(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    await store.put("t/w/files/a.txt", b"hi")
    assert (tmp_path / "t" / "w" / "files" / "a.txt").read_bytes() == b"hi"


async def test_localfs_put_overwrites(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    await store.put("k", b"one")
    await store.put("k", b"two")
    assert await store.get("k") == b"two"


async def test_localfs_exists(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    assert await store.exists("k") is False
    await store.put("k", b"x")
    assert await store.exists("k") is True


async def test_localfs_get_missing_raises(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        await store.get("nope")


async def test_localfs_delete(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    await store.put("k", b"x")
    await store.delete("k")
    assert await store.exists("k") is False


async def test_localfs_delete_missing_is_noop(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    await store.delete("never-existed")  # must not raise


async def test_localfs_rejects_path_traversal(tmp_path):
    store = LocalFsObjectStore(root=str(tmp_path))
    with pytest.raises(ValueError):
        await store.put("../escape", b"x")


async def test_localfs_creates_root(tmp_path):
    root = tmp_path / "nested" / "objects"
    LocalFsObjectStore(root=str(root))
    assert root.is_dir()


# --------------------------------------------------------------------- S3 ----


class _FakeS3Client:
    """Minimal in-memory stand-in for a boto3 S3 client."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple] = []

    def put_object(self, **kw):
        self.calls.append(("put_object", kw["Bucket"], kw["Key"], kw.get("ContentType")))
        self.objects[kw["Key"]] = kw["Body"]

    def get_object(self, **kw):
        self.calls.append(("get_object", kw["Bucket"], kw["Key"]))
        if kw["Key"] not in self.objects:
            raise _client_error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}

    def delete_object(self, **kw):
        self.calls.append(("delete_object", kw["Bucket"], kw["Key"]))
        self.objects.pop(kw["Key"], None)

    def head_object(self, **kw):
        self.calls.append(("head_object", kw["Bucket"], kw["Key"]))
        if kw["Key"] not in self.objects:
            raise _client_error("404")
        return {"ContentLength": len(self.objects[kw["Key"]])}


def _client_error(code: str):
    return s3_module.ClientError({"Error": {"Code": code}}, "op")


@pytest.fixture
def fake_s3(monkeypatch):
    client = _FakeS3Client()
    monkeypatch.setattr(s3_module.boto3, "client", lambda *a, **k: client)
    return client


async def test_s3_put_sends_full_key_and_content_type(fake_s3):
    store = S3ObjectStore(bucket="b", prefix="canon")
    await store.put("t/w/files/a.txt", b"hello", content_type="text/plain")
    assert fake_s3.calls[0] == ("put_object", "b", "canon/t/w/files/a.txt", "text/plain")
    assert fake_s3.objects["canon/t/w/files/a.txt"] == b"hello"


async def test_s3_get_roundtrip(fake_s3):
    store = S3ObjectStore(bucket="b", prefix="canon")
    await store.put("k", b"payload")
    assert await store.get("k") == b"payload"
    assert ("get_object", "b", "canon/k") in fake_s3.calls


async def test_s3_no_prefix(fake_s3):
    store = S3ObjectStore(bucket="b")
    await store.put("k", b"x")
    assert "k" in fake_s3.objects


async def test_s3_get_missing_raises_filenotfound(fake_s3):
    store = S3ObjectStore(bucket="b")
    with pytest.raises(FileNotFoundError):
        await store.get("nope")


async def test_s3_exists(fake_s3):
    store = S3ObjectStore(bucket="b", prefix="canon")
    assert await store.exists("k") is False
    await store.put("k", b"x")
    assert await store.exists("k") is True
    assert ("head_object", "b", "canon/k") in fake_s3.calls


async def test_s3_delete(fake_s3):
    store = S3ObjectStore(bucket="b")
    await store.put("k", b"x")
    await store.delete("k")
    assert ("delete_object", "b", "k") in fake_s3.calls
    assert await store.exists("k") is False


async def test_s3_rejects_path_traversal(fake_s3):
    store = S3ObjectStore(bucket="b")
    with pytest.raises(ValueError):
        await store.put("a/../b", b"x")


# ---------------------------------------------------------------- factory ----


def test_factory_default_is_localfs(tmp_path):
    settings = CanonSettings(object_store_localfs_root=str(tmp_path))
    store = build_object_store(settings)
    assert isinstance(store, LocalFsObjectStore)
    assert isinstance(store, ObjectStore)


def test_factory_builds_s3(monkeypatch):
    monkeypatch.setattr(s3_module.boto3, "client", lambda *a, **k: _FakeS3Client())
    settings = CanonSettings(object_store_backend="s3", object_store_s3_bucket="b")
    store = build_object_store(settings)
    assert isinstance(store, S3ObjectStore)


def test_factory_unknown_backend_raises():
    settings = CanonSettings(object_store_backend="azure")
    with pytest.raises(ValueError):
        build_object_store(settings)
