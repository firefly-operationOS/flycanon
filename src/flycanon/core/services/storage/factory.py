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

"""Build the configured ObjectStore backend from CanonSettings."""

from __future__ import annotations

from flycanon.config import CanonSettings
from flycanon.core.services.storage.local_fs import LocalFsObjectStore
from flycanon.core.services.storage.object_store import ObjectStore
from flycanon.core.services.storage.s3 import S3ObjectStore


def build_object_store(settings: CanonSettings) -> ObjectStore:
    """Return the ObjectStore selected by ``FLYCANON_OBJECT_STORE_BACKEND``."""
    backend = settings.object_store_backend
    if backend == "localfs":
        return LocalFsObjectStore(root=settings.object_store_localfs_root)
    if backend == "s3":
        return S3ObjectStore(
            bucket=settings.object_store_s3_bucket,
            prefix=settings.object_store_s3_prefix,
            endpoint_url=settings.object_store_s3_endpoint_url or None,
            region=settings.object_store_s3_region or None,
        )
    raise ValueError(f"unknown object_store_backend: {backend!r}")
