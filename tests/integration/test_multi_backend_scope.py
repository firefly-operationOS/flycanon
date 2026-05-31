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

"""Cross-scope isolation for the external dense backends.

Proves that :class:`TenantScopedVectorStore` confines reads/writes to a
``(tenant_id, workspace_id)`` scope when wrapping a REAL external backend (not a
fake): Chroma in-process, and Qdrant via a container when one is available. The
scope wrapper is backend-agnostic, so these exercise the same code path flycanon
uses for every non-pgvector backend.
"""

from __future__ import annotations

import uuid

import pytest
from fireflyframework_agentic.vectorstores import TenantScopedVectorStore
from fireflyframework_agentic.vectorstores.types import VectorDocument

pytestmark = pytest.mark.integration


def _docs(id_: str, vec: list[float]) -> list[VectorDocument]:
    return [VectorDocument(id=id_, text=f"doc-{id_}", embedding=vec)]


class TestChromaScopeIsolation:
    async def test_scope_isolation_through_wrapper(self) -> None:
        chromadb = pytest.importorskip("chromadb")
        from fireflyframework_agentic.vectorstores import ChromaVectorStore

        collection = f"canon_{uuid.uuid4().hex[:8]}"
        store = TenantScopedVectorStore(
            ChromaVectorStore(collection_name=collection, client=chromadb.EphemeralClient())
        )
        await store.upsert(_docs("a", [1.0, 0.0, 0.0]), tenant_id="acme", workspace_id="ws-a")
        await store.upsert(_docs("b", [1.0, 0.0, 0.0]), tenant_id="acme", workspace_id="ws-b")

        mine = await store.search([1.0, 0.0, 0.0], top_k=5, tenant_id="acme", workspace_id="ws-a")
        assert [r.document.id for r in mine] == ["a"]

        foreign = await store.search([1.0, 0.0, 0.0], top_k=5, tenant_id="nobody", workspace_id="ws-a")
        assert foreign == []

    async def test_delete_is_scoped(self) -> None:
        chromadb = pytest.importorskip("chromadb")
        from fireflyframework_agentic.vectorstores import ChromaVectorStore

        collection = f"canon_{uuid.uuid4().hex[:8]}"
        store = TenantScopedVectorStore(
            ChromaVectorStore(collection_name=collection, client=chromadb.EphemeralClient())
        )
        await store.upsert(_docs("a", [1.0, 0.0, 0.0]), tenant_id="acme", workspace_id="ws-a")
        await store.delete(["a"], tenant_id="acme", workspace_id="ws-a")
        remaining = await store.search([1.0, 0.0, 0.0], top_k=5, tenant_id="acme", workspace_id="ws-a")
        assert remaining == []


class TestQdrantScopeIsolation:
    @pytest.fixture(scope="class")
    def qdrant_url(self) -> str:
        pytest.importorskip("qdrant_client")
        try:
            from testcontainers.core.container import DockerContainer
            from testcontainers.core.waiting_utils import wait_for_logs
        except ImportError:  # pragma: no cover
            pytest.skip("testcontainers not installed")
        try:
            container = DockerContainer("qdrant/qdrant:latest").with_exposed_ports(6333)
            container.start()
        except Exception as exc:  # pragma: no cover - docker/image unavailable
            pytest.skip(f"qdrant container unavailable: {exc}")
        try:
            wait_for_logs(container, "Qdrant gRPC listening", timeout=60)
            host = container.get_container_host_ip()
            port = container.get_exposed_port(6333)
            yield f"http://{host}:{port}"
        finally:
            container.stop()

    async def test_scope_isolation_through_wrapper(self, qdrant_url: str) -> None:
        from fireflyframework_agentic.vectorstores import QdrantVectorStore

        collection = f"canon_{uuid.uuid4().hex[:8]}"
        store = TenantScopedVectorStore(
            QdrantVectorStore(collection_name=collection, url=qdrant_url, vector_size=3)
        )
        await store.initialise()
        # Qdrant point ids must be UUIDs/uints; flycanon chunk ids are UUIDs.
        id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
        try:
            await store.upsert(_docs(id_a, [1.0, 0.0, 0.0]), tenant_id="acme", workspace_id="ws-a")
            await store.upsert(_docs(id_b, [1.0, 0.0, 0.0]), tenant_id="acme", workspace_id="ws-b")

            mine = await store.search([1.0, 0.0, 0.0], top_k=5, tenant_id="acme", workspace_id="ws-a")
            assert [r.document.id for r in mine] == [id_a]

            foreign = await store.search([1.0, 0.0, 0.0], top_k=5, tenant_id="nobody", workspace_id="ws-a")
            assert foreign == []
        finally:
            await store.close()
