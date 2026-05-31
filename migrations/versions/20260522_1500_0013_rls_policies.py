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

"""Enable Postgres row-level security on every canon_* table.

Revision ID: 0013_rls_policies
Revises: 0012_agent_tokens
Create Date: 2026-05-22

Postgres-only. SQLite (used by unit tests) is a no-op.

For each canon_* table with ``(tenant_id, workspace_id)`` columns:

1. ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY``.
2. ``ALTER TABLE ... FORCE ROW LEVEL SECURITY`` (so even the owner
   role is subject to the policy unless it has BYPASSRLS).
3. Create a ``tenant_workspace_isolation`` policy that matches
   both columns against GUCs ``app.tenant_id`` / ``app.workspace_id``.

Special cases:

- ``canon_workspaces`` has ``tenant_id`` but its ``id`` column IS
  the workspace identity, so the policy matches ``tenant_id`` and
  ``id = app.workspace_id`` (covers GET /workspaces/{id} and the
  internal lookups; LIST is bypassed via BYPASSRLS for the workspace
  controller's admin path).
- ``canon_agent_tokens`` has ``tenant_id`` but NO workspace column
  (a token can serve multiple workspaces via its allowlist) -- policy
  matches ``tenant_id`` only.
- ``canon_chunk_vectors`` is the runtime-created pgvector table; the
  migration MUST guard with ``IF EXISTS`` because the table may not
  exist on first-deploy until the PgvectorStore boots.

Application sessions ``SET LOCAL`` the GUCs at transaction start via
the session-factory wrapper in ``flycanon.web.conventions.db``.

Migrations + ops tooling use a separate role with ``BYPASSRLS``
(deployment responsibility -- not created here; document in
``docs/architecture.md``).
"""

from __future__ import annotations

from alembic import op

revision = "0013_rls_policies"
down_revision = "0012_agent_tokens"
branch_labels = None
depends_on = None


# Tables with (tenant_id, workspace_id) -- standard policy.
_SCOPED_TABLES = [
    "canon_audit_events",
    "canon_candidates",
    "canon_chunks",
    "canon_citations",
    "canon_conversation_turns",
    "canon_conversations",
    "canon_cost_events",
    "canon_ingest_job_events",
    "canon_ingest_jobs",
    "canon_knowledge_items",
    "canon_knowledge_relations",
    "canon_knowledge_versions",
    "canon_sources",
    "canon_taxonomy_nodes",
]


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return

    # Standard (tenant_id, workspace_id) policy.
    for table in _SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_workspace_isolation ON {table}
              USING (
                tenant_id = current_setting('app.tenant_id', true)
                AND workspace_id = current_setting('app.workspace_id', true)
              )
            """
        )

    # canon_workspaces -- id IS the workspace identity.
    op.execute("ALTER TABLE canon_workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE canon_workspaces FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_workspace_isolation ON canon_workspaces
          USING (
            tenant_id = current_setting('app.tenant_id', true)
            AND id = current_setting('app.workspace_id', true)
          )
        """
    )

    # canon_agent_tokens -- tenant-only.
    op.execute("ALTER TABLE canon_agent_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE canon_agent_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON canon_agent_tokens
          USING (
            tenant_id = current_setting('app.tenant_id', true)
          )
        """
    )

    # canon_chunk_vectors -- runtime-created; guard with IF EXISTS via
    # DO block (alembic doesn't have IF EXISTS for ALTER TABLE).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'canon_chunk_vectors'
            ) THEN
                EXECUTE 'ALTER TABLE canon_chunk_vectors ENABLE ROW LEVEL SECURITY';
                EXECUTE 'ALTER TABLE canon_chunk_vectors FORCE ROW LEVEL SECURITY';
                EXECUTE $POLICY$
                    CREATE POLICY tenant_workspace_isolation ON canon_chunk_vectors
                      USING (
                        tenant_id = current_setting('app.tenant_id', true)
                        AND workspace_id = current_setting('app.workspace_id', true)
                      )
                $POLICY$;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    if not _is_postgres():
        return

    for table in _SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_workspace_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_workspace_isolation ON canon_workspaces")
    op.execute("ALTER TABLE canon_workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE canon_workspaces DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON canon_agent_tokens")
    op.execute("ALTER TABLE canon_agent_tokens NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE canon_agent_tokens DISABLE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'canon_chunk_vectors'
            ) THEN
                EXECUTE 'DROP POLICY IF EXISTS tenant_workspace_isolation ON canon_chunk_vectors';
                EXECUTE 'ALTER TABLE canon_chunk_vectors NO FORCE ROW LEVEL SECURITY';
                EXECUTE 'ALTER TABLE canon_chunk_vectors DISABLE ROW LEVEL SECURITY';
            END IF;
        END
        $$;
        """
    )
