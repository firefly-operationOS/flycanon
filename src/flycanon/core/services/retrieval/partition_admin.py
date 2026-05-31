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

"""Tier-B partition-by-tenant escape valve for ``canon_chunk_vectors``.

DORMANT BY DEFAULT. Production deployments stay on Tier-A
(global HNSW + ef_search bump + WHERE filter) until a single
tenant outgrows the global index -- empirically ~500k+ chunks.

When an admin needs to enable Tier-B for a hot tenant, they:

1. (Once, deployment-wide) Migrate ``canon_chunk_vectors`` to
   declarative partitioning. This requires downtime. Pattern::

       -- Capture the existing rows.
       CREATE TABLE canon_chunk_vectors_legacy AS
         TABLE canon_chunk_vectors;
       DROP TABLE canon_chunk_vectors;

       -- Recreate as a partitioned table.
       CREATE TABLE canon_chunk_vectors (
           id TEXT PRIMARY KEY,
           namespace TEXT NOT NULL DEFAULT 'default',
           tenant_id TEXT NOT NULL DEFAULT 'default',
           workspace_id TEXT NOT NULL DEFAULT 'default',
           embedding vector(<dim>) NOT NULL,
           metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
           text TEXT NOT NULL,
           created_at TIMESTAMPTZ NOT NULL DEFAULT now()
       ) PARTITION BY LIST (tenant_id);

       -- Default partition catches rows for tenants without a
       -- dedicated partition.
       CREATE TABLE canon_chunk_vectors_default
           PARTITION OF canon_chunk_vectors DEFAULT;

       -- HNSW index on the default partition (Tier-A coverage).
       CREATE INDEX canon_chunk_vectors_default_hnsw
           ON canon_chunk_vectors_default
           USING hnsw (embedding vector_cosine_ops);

       -- Re-insert legacy rows; Postgres routes by tenant_id.
       INSERT INTO canon_chunk_vectors SELECT * FROM canon_chunk_vectors_legacy;
       DROP TABLE canon_chunk_vectors_legacy;

2. (Per hot tenant) Call ``promote_tenant_to_partition(engine,
   tenant_id)`` below. It creates a dedicated partition + a
   per-partition HNSW index for that tenant. The retrieval
   service stays unchanged -- Postgres routes by the
   ``tenant_id = ?`` predicate to the right partition.

3. (Per cooled-down tenant) Call
   ``demote_tenant_from_partition(engine, tenant_id)`` to merge
   the partition back into the default. Re-uses Postgres'
   declarative partition detach pattern.

This module is the operational interface for Tier-B; production
clusters stay on Tier-A unless one of the two `promote_*`
helpers is explicitly invoked.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flycanon.web.conventions.validation import validate_slug


def _partition_name(tenant_id: str) -> str:
    """Return the Postgres partition table name for ``tenant_id``.

    Sanitises via the conventions slug validator so we never embed
    untrusted input into raw DDL.
    """
    safe = validate_slug(tenant_id)
    return f"canon_chunk_vectors_{safe}"


async def is_partitioned(engine: AsyncEngine) -> bool:
    """Check whether ``canon_chunk_vectors`` is declarative-partitioned.

    Returns ``False`` if the table is missing or non-partitioned.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT 1
                FROM pg_class c
                JOIN pg_partitioned_table pt ON pt.partrelid = c.oid
                WHERE c.relname = 'canon_chunk_vectors'
                """
            )
        )
        return result.scalar() is not None


async def promote_tenant_to_partition(engine: AsyncEngine, tenant_id: str) -> None:
    """Carve out a dedicated partition + HNSW index for ``tenant_id``.

    Pre-conditions:
    - ``canon_chunk_vectors`` is declared
      ``PARTITION BY LIST (tenant_id)`` (see the module docstring
      for the one-time setup).
    - The caller has admin privileges.

    Idempotent: calling for an already-promoted tenant is a no-op.

    Raises:
        :class:`InvalidSlugError` if ``tenant_id`` fails the slug
            grammar.
        :class:`RuntimeError` if the table is not partitioned.
    """
    partition = _partition_name(tenant_id)
    if not await is_partitioned(engine):
        raise RuntimeError(
            "canon_chunk_vectors is not declarative-partitioned. "
            "Run the one-time setup before promoting tenants."
        )
    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT 1 FROM pg_class WHERE relname = :rel"),
            {"rel": partition},
        )
        if existing.scalar() is not None:
            return
        # CREATE TABLE inside a transaction. asyncpg rejects bind parameters
        # in DDL at the wire-protocol level ("parameters are supported only
        # in SELECT/INSERT/UPDATE/DELETE/MERGE/VALUES"), so we inline the
        # tenant slug after validating it through the slug grammar
        # (``^[a-z0-9][a-z0-9_-]{0,63}$``) -- no SQL metacharacter can survive
        # that, which is what makes the literal embed safe here.
        safe_tenant_id = validate_slug(tenant_id)
        await conn.execute(
            text(
                f"""
                CREATE TABLE {partition}
                PARTITION OF canon_chunk_vectors
                FOR VALUES IN ('{safe_tenant_id}')
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE INDEX {partition}_hnsw
                ON {partition}
                USING hnsw (embedding vector_cosine_ops)
                """
            )
        )


async def demote_tenant_from_partition(engine: AsyncEngine, tenant_id: str) -> None:
    """Detach + drop the per-tenant partition, returning rows to the
    default partition.

    Pre-conditions: same as ``promote_tenant_to_partition``.

    Idempotent: calling for a tenant without a dedicated partition
    is a no-op.
    """
    partition = _partition_name(tenant_id)
    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT 1 FROM pg_class WHERE relname = :rel"),
            {"rel": partition},
        )
        if existing.scalar() is None:
            return
        # ALTER ... DETACH PARTITION returns the partition to a
        # standalone table; rows are reattached to the default by
        # the partition routing on subsequent INSERTs. To merge in
        # one step we INSERT-then-DROP.
        await conn.execute(text(f"ALTER TABLE canon_chunk_vectors DETACH PARTITION {partition}"))
        await conn.execute(text(f"INSERT INTO canon_chunk_vectors SELECT * FROM {partition}"))
        await conn.execute(text(f"DROP TABLE {partition}"))
