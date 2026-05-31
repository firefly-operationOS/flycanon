# Copyright 2026 Firefly Software Solutions Inc
"""Drop the legacy column-shaped canon_chunk_vectors table.

Revision ID: 0014_drop_legacy_chunk_vectors
Revises: 0013_rls_policies
Create Date: 2026-05-31

The dense vector projection moved to the framework's namespace-based pgvector
adapter (``fireflyframework_agentic.vectorstores.PgVectorVectorStore``), wrapped
by flycanon's :class:`RlsPgVectorVectorStore`. The new table shape is
namespace-centric -- ``(id, namespace, embedding, text, metadata, created_at)``
-- with a **namespace-keyed** RLS policy (``app.scope_namespace`` GUC), replacing
the old ``(tenant_id, workspace_id)`` columns + composite policy.

``canon_chunk_vectors`` is a DERIVED projection (``canon_chunks`` is the
system-of-record), so it is dropped here rather than migrated in place; the
adapter recreates it in the new shape on first boot and a **re-index**
repopulates it. Dropping the table also removes the old column-based RLS policy
so it cannot shadow the new namespace-based one (same policy name).

On a fresh database the table does not exist yet (it is runtime-created), so
this is a no-op. On SQLite (tests) it is also a no-op.

ACTION REQUIRED on deploy: re-index existing sources after upgrading so the
dense projection is rebuilt in the new table.
"""

from __future__ import annotations

from alembic import op

revision = "0014_drop_legacy_chunk_vectors"
down_revision = "0013_rls_policies"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    # CASCADE removes the old column-based RLS policy + indexes with the table.
    op.execute("DROP TABLE IF EXISTS canon_chunk_vectors CASCADE")


def downgrade() -> None:
    # The table is runtime-created by the vector store and holds a derived
    # projection; there is nothing to restore here. The (older) adapter would
    # recreate it on next boot.
    return
