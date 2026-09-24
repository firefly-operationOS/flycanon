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

"""Bulk deletion of every scoped row for one ``(tenant_id, workspace_id)``.

Backs ``POST /api/v1/workspaces/{id}:purge``. The per-source pipeline
(:meth:`IntakeService.remove`) already takes care of chunks, dense
vectors, stored originals and the source row itself, one source at a
time, with its own audit row and EDA event -- the purge service drives
that first. What remains afterwards is everything that hangs off the
scope but not off a source: knowledge items with their versions,
citations and relations; candidates; conversations and their turns;
ingest jobs and their events; cost events; and any chunk row the
per-source pass could not reach because its source row was already
gone. This repository deletes those in dependency order inside ONE
transaction so a failure part-way leaves the scope intact and the call
retryable.

Deliberately NOT deleted:

* ``canon_audit_events`` -- the audit log is append-only by contract
  (see docs/security-model.md). The purge writes its own
  ``workspace.purged`` row there; erasing the trail of a purge would
  defeat the reason the trail exists.
* ``canon_taxonomy_nodes`` -- the domain/jurisdiction seed is
  workspace-scoped but structural; a workspace that is re-opened
  after a purge expects its taxonomy back, and the rows hold no
  tenant content.
* ``canon_agent_tokens`` -- tokens are tenant-level, not workspace-level
  (a token may serve several workspaces via its allowlist). Revoking
  them is the caller's decision through ``DELETE /api/v1/agent-tokens``.

The ORM tables are deleted through their mapped classes rather than
raw ``DELETE FROM`` strings so a renamed column fails at import time,
not at the first production purge.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flycanon.models.entities.candidate import CandidateRow
from flycanon.models.entities.citation import CitationRow
from flycanon.models.entities.conversation import ConversationRow, ConversationTurnRow
from flycanon.models.entities.cost_event import CostEventRow
from flycanon.models.entities.ingest_job import IngestJobEventRow, IngestJobRow
from flycanon.models.entities.knowledge_chunk import KnowledgeChunkRow
from flycanon.models.entities.knowledge_item import KnowledgeItemRow
from flycanon.models.entities.knowledge_relation import KnowledgeRelationRow
from flycanon.models.entities.knowledge_version import KnowledgeVersionRow
from flycanon.models.repositories._engine import build_session_factory

#: ``(report key, mapped class)`` in the order the deletes must run:
#: children before parents so the statement never trips a foreign key
#: on a database that enforces them (Postgres does; the SQLite test
#: engine does not, which is exactly why the order is fixed here and
#: not left to ``ON DELETE CASCADE``).
_PURGE_ORDER: tuple[tuple[str, type[Any]], ...] = (
    ("citations", CitationRow),
    ("knowledge_relations", KnowledgeRelationRow),
    ("knowledge_versions", KnowledgeVersionRow),
    ("knowledge_items", KnowledgeItemRow),
    ("candidates", CandidateRow),
    ("conversation_turns", ConversationTurnRow),
    ("conversations", ConversationRow),
    ("ingest_job_events", IngestJobEventRow),
    ("ingest_jobs", IngestJobRow),
    ("cost_events", CostEventRow),
    ("chunks", KnowledgeChunkRow),
)


class ScopePurgeRepository:
    """Delete every non-source row owned by one tenant/workspace pair."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    @classmethod
    def from_url(cls, database_url: str, *, echo: bool = False) -> ScopePurgeRepository:
        factory, engine = build_session_factory(database_url, echo=echo)
        return cls(factory, engine=engine)

    async def purge_scope(self, *, tenant_id: str, workspace_id: str) -> dict[str, int]:
        """Delete the scope's rows in one transaction; return ``{table_key: rows}``.

        Both scope keys are mandatory and are matched on every table --
        there is no "all workspaces of the tenant" variant on purpose:
        a tenant off-boarding calls this once per workspace it knows
        about, so a typo in one id can only ever empty one workspace.
        """
        if not tenant_id or not workspace_id:
            raise ValueError("tenant_id and workspace_id are required to purge a scope")
        counts: dict[str, int] = {}
        async with self._session_factory() as session, session.begin():
            for key, mapped in _PURGE_ORDER:
                stmt = delete(mapped).where(
                    mapped.tenant_id == tenant_id,
                    mapped.workspace_id == workspace_id,
                )
                result = await session.execute(stmt)
                counts[key] = int(getattr(result, "rowcount", 0) or 0)
        return counts


__all__ = ["ScopePurgeRepository"]
