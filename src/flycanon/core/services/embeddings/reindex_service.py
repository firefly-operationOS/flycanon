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

"""``flycanon reindex`` -- re-embed a corpus into a new embedding set.

The verb ``docs/deployment.md`` and ``docs/operations-runbook.md`` have been
telling operators to run since 26.5, and which the software did not have. It
exists because of what migration ``0017`` made possible: several embedding
spaces in one table, so a change of embedder is a batch job with a rollback
instead of a fresh database.

What a run does
---------------
1. **Plan and estimate.** Resolve the scope, count the chunks, cap each one at
   the embedder's input limit, and price it -- or say ``cost unknown``, never
   ``$0.00``.
2. **Create one set per workspace**, ``status=building``. The id is printed
   immediately, because it is the ``--resume`` handle.
3. **Embed in batches**, reading from ``canon_chunks`` (the system of record --
   no re-chunking, no re-downloading originals) and upserting into the new
   ``set_id``. Each batch commits its vectors, stamps ``embedding_model`` on
   the chunks, and advances the cursor in the job's ``metadata_json``, so a
   killed run resumes from where it stopped and a replayed batch rewrites the
   same rows (``ON CONFLICT (set_id, id)``).
4. **Catch-up pass.** Chunks created after the set was created are embedded
   before the switch, so a document ingested during the run is not missing
   from the set that is about to start answering queries. This is the cheapest
   of the three mitigations for the write-path gap; genuine dual-write in
   ``IndexService`` is the zero-downtime answer and is not here.
5. **Build the ANN index**, then ``status=ready``.
6. **Activate**, unless ``--no-activate``: one UPDATE of
   ``canon_workspaces.active_embedding_set_id``. The old set is retired, and
   its rows and index are kept, so ``--rollback`` is the same UPDATE backwards.

What serves searches while it runs: **the old set, unchanged, throughout.**
The new set is invisible to search until it is activated, because the search
predicate IS the set id.

RLS, and the failure this programme has already hit once
--------------------------------------------------------
A reindex touches two GUC families that are not the same thing:
``app.tenant_id`` + ``app.workspace_id`` for ``canon_chunks`` /
``canon_embedding_sets`` (migration 0013), and ``app.scope_namespace`` for
``canon_chunk_vectors`` (migration 0016). Both must be in force for every read
and write or the job sees an empty corpus and reports success -- the exact
``hits: []``-under-``flycanon_app`` shape debugged on 2026-09-17. So the
service binds a :class:`TenantContext` per workspace (which the ORM's
``after_begin`` listener turns into the first pair) while the dense store sets
the second itself, and a workspace that reads ZERO chunks when its
``canon_chunks`` count says otherwise **fails the run** instead of finishing it.

Where this stops short, said plainly
-------------------------------------
Batches run in the CLI process, not on the ingest worker. That is a deliberate
trade: ``FLYCANON_WORKER_HANDLER_TIMEOUT_S`` defaults to 120 s, so a
worker-dispatched re-embed has to be one event per batch with its own retry
accounting, and a CLI has no such wall. The cost is that a run needs a shell
to stay open; the cursor is what makes that survivable. The job and event rows
are written either way, so the existing SSE progress surface works unchanged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fireflyframework_agentic.vectorstores.types import VectorDocument

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.embedding_service import EmbeddingRegistry, EmbeddingThrottled
from flycanon.core.services.embeddings.embedding_sets import (
    EmbeddingSetBinding,
    EmbeddingSetError,
    EmbeddingSetService,
    bind_embedding_set,
)
from flycanon.core.services.embeddings.model_capabilities import validate_dimensions
from flycanon.core.services.embeddings.prices import price_for, render_cost
from flycanon.models.entities.ingest_job import IngestJobRow
from flycanon.models.repositories.chunk_repository import ChunkRepository
from flycanon.models.repositories.ingest_job_repository import IngestJobRepository
from flycanon.web.conventions.context import TenantContext, set_tenant_context

logger = logging.getLogger(__name__)

#: Chars-per-token proxy. The same one ``BaseEmbedder`` uses to report token
#: counts, so the estimate and the eventual bill are at least commensurable.
_CHARS_PER_TOKEN = 4


@dataclass(slots=True)
class WorkspacePlan:
    """What a reindex would do to one workspace."""

    tenant_id: str
    workspace_id: str
    chunk_count: int
    estimated_tokens: int
    current_model: str | None
    current_dimensions: int | None


@dataclass(slots=True)
class ReindexPlan:
    """The whole run, before anything is written."""

    provider: str
    model: str
    dimensions: int
    workspaces: list[WorkspacePlan] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return sum(w.chunk_count for w in self.workspaces)

    @property
    def estimated_tokens(self) -> int:
        return sum(w.estimated_tokens for w in self.workspaces)

    @property
    def embedding_model(self) -> str:
        return f"{self.provider}:{self.model}"

    def cost_line(self) -> str:
        return render_cost(provider=self.provider, model=self.model, tokens=self.estimated_tokens)

    def render(self) -> str:
        """The block an operator reads before answering ``Proceed? [y/N]``."""
        lines = [
            f"reindex plan: {len(self.workspaces)} workspace(s), {self.chunk_count} chunk(s), "
            f"~{self.estimated_tokens:,} tokens",
            f"target:    {self.embedding_model} @{self.dimensions}",
            f"estimated: {self.cost_line()}   |  index rebuild: {len(self.workspaces)} HNSW index(es)",
        ]
        current = sorted(
            {f"{w.current_model} @{w.current_dimensions}" for w in self.workspaces if w.current_model}
        )
        lines.append(f"current:   {', '.join(current) if current else 'no embedding set yet'}")
        if price_for(provider=self.provider, model=self.model) is None:
            lines.append(
                "note:      this model has no price row, so the cost above is UNKNOWN, not zero. "
                "Add one in flycanon.core.services.embeddings.prices before quoting a figure."
            )
        return "\n".join(lines)


@dataclass(slots=True)
class SetListing:
    """One row of ``flycanon reindex --list``."""

    tenant_id: str
    workspace_id: str
    row: Any
    is_active: bool


@dataclass(slots=True)
class WorkspaceOutcome:
    """What a reindex actually did to one workspace."""

    tenant_id: str
    workspace_id: str
    set_id: str
    embedded: int
    activated: bool
    retired_set_id: str | None
    indexed: bool
    elapsed_s: float
    status: str


class ReindexError(Exception):
    """Raised when a reindex cannot start, or must stop."""


class ReindexService:
    """Plan, run, activate, roll back and drop embedding sets."""

    def __init__(
        self,
        *,
        chunks: ChunkRepository,
        jobs: IngestJobRepository,
        sets: EmbeddingSetService,
        registry: EmbeddingRegistry,
        vector_store: Any,
        dense_backend: Any,
        settings: CanonSettings,
    ) -> None:
        self._chunks = chunks
        self._jobs = jobs
        self._sets = sets
        self._registry = registry
        self._vector_store = vector_store
        self._dense = dense_backend
        self._settings = settings

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    async def resolve_scope(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        everything: bool = False,
    ) -> list[tuple[str, str]]:
        """The ``(tenant, workspace)`` pairs in scope. Exactly one selector.

        The three selectors are ONE workspace (which needs its tenant, because
        a workspace id is scoped to one), one whole tenant, and everything.
        ``--workspace`` plus ``--tenant`` is the first of those, not two.

        There is deliberately no bare ``flycanon reindex`` that silently means
        ``--all``: re-embedding every tenant of a shared deployment is a
        decision, and it should read like one in the shell history.
        """
        if workspace_id and everything:
            raise ReindexError("--workspace and --all are different scopes; choose one")
        if workspace_id:
            if not tenant_id:
                raise ReindexError("--workspace needs --tenant: a workspace id is scoped to a tenant")
            return [(tenant_id, workspace_id)]
        if tenant_id and everything:
            raise ReindexError("--tenant and --all are different scopes; choose one")
        if not tenant_id and not everything:
            raise ReindexError("choose a scope: --workspace <id> --tenant <id>, --tenant <id>, or --all")
        return await self._chunks.scopes_with_chunks(tenant_id=tenant_id if not everything else None)

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    async def plan(
        self,
        *,
        scopes: list[tuple[str, str]],
        provider: str,
        model: str,
        dimensions: int,
    ) -> ReindexPlan:
        """Validate the target and price the work, writing nothing."""
        validate_dimensions(provider=provider, model=model, dimensions=dimensions)
        # Build the embedder now, so a provider that will not import, or an
        # Azure endpoint that is not configured, fails before a single set row
        # exists rather than after the first workspace is half done.
        self._registry.for_model(provider=provider, model=model, dimensions=dimensions)

        plan = ReindexPlan(provider=provider, model=model, dimensions=dimensions)
        max_chars = self._registry.default.max_input_chars
        for tenant_id, workspace_id in scopes:
            with ScopedContext(tenant_id, workspace_id):
                count = await self._chunks.count_for_workspace(tenant_id=tenant_id, workspace_id=workspace_id)
                chars = await self._chunks.input_chars_for_workspace(
                    tenant_id=tenant_id, workspace_id=workspace_id, max_input_chars=max_chars
                )
                current = await self._sets.active_binding(tenant_id=tenant_id, workspace_id=workspace_id)
            plan.workspaces.append(
                WorkspacePlan(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    chunk_count=count,
                    estimated_tokens=chars // _CHARS_PER_TOKEN,
                    current_model=current.embedding_model if current else None,
                    current_dimensions=current.dimensions if current else None,
                )
            )
        return plan

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(
        self,
        plan: ReindexPlan,
        *,
        batch_size: int = 256,
        activate: bool = True,
        resume_set_id: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[WorkspaceOutcome]:
        outcomes: list[WorkspaceOutcome] = []
        for workspace in plan.workspaces:
            outcomes.append(
                await self._run_workspace(
                    workspace,
                    plan=plan,
                    batch_size=batch_size,
                    activate=activate,
                    resume_set_id=resume_set_id,
                    on_progress=on_progress,
                )
            )
        return outcomes

    async def _run_workspace(
        self,
        workspace: WorkspacePlan,
        *,
        plan: ReindexPlan,
        batch_size: int,
        activate: bool,
        resume_set_id: str | None,
        on_progress: Callable[[str], None] | None,
    ) -> WorkspaceOutcome:
        started = time.perf_counter()
        tenant_id, workspace_id = workspace.tenant_id, workspace.workspace_id
        emit = on_progress or (lambda _line: None)

        with ScopedContext(tenant_id, workspace_id):
            binding, job, cursor = await self._open(
                workspace=workspace, plan=plan, resume_set_id=resume_set_id
            )
            emit(f"{workspace_id}: set {binding.set_id} ({binding.embedding_model} @{binding.dimensions})")

            # The embedder for the TARGET set, in strict mode: a zero vector
            # written here is a permanently wrong row in the set that is about
            # to start answering queries, whatever the deployment's
            # FLYCANON_EMBEDDING_ZERO_VECTOR_ON_FAILURE says.
            embedder = self._registry.for_binding(binding).strict()

            embedded = int(cursor.get("done", 0))
            last_id: str | None = cursor.get("last_chunk_id")
            expected = workspace.chunk_count
            read_any = embedded > 0

            while True:
                batch = await self._chunks.list_for_workspace(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    after_id=last_id,
                    limit=batch_size,
                )
                if not batch:
                    break
                read_any = True
                try:
                    written = await self._embed_batch(
                        batch,
                        binding=binding,
                        embedder=embedder,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                    )
                except EmbeddingThrottled as exc:
                    # A throttle is a state, not a failure: the cursor still
                    # points at the last committed batch, ``attempts`` is
                    # untouched, and `--resume` continues from here. Reserving
                    # attempts for genuine errors is what stops
                    # ingest_max_attempts=3 quietly killing a long run against
                    # a small TPM quota.
                    await self._record(
                        job,
                        stage="reindex.throttled",
                        message=str(exc),
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        payload={"retry_after_s": exc.retry_after},
                    )
                    await self._sets.mark(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        set_id=binding.set_id,
                        status="building",
                        vector_count=embedded,
                    )
                    emit(
                        f"{workspace_id}: throttled after {embedded} chunk(s); resume with "
                        f"--resume {binding.set_id}"
                    )
                    return WorkspaceOutcome(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        set_id=binding.set_id,
                        embedded=embedded,
                        activated=False,
                        retired_set_id=None,
                        indexed=False,
                        elapsed_s=time.perf_counter() - started,
                        status="throttled",
                    )
                embedded += written
                last_id = batch[-1].id
                await self._advance(
                    job,
                    set_id=binding.set_id,
                    last_chunk_id=last_id,
                    done=embedded,
                    total=expected,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                emit(f"{workspace_id}: {embedded}/{expected} chunk(s)")

            if expected > 0 and not read_any:
                # The 2026-09-17 bug, as a refusal. A NOBYPASSRLS role whose
                # GUCs are not in force reads an empty corpus and would
                # otherwise finish "successfully" with an empty set, then
                # activate it.
                raise ReindexError(
                    f"workspace {workspace_id} holds {expected} chunk(s) but the reindex read 0. "
                    "The run is under a role that cannot see them: check that the DSN's role "
                    "bypasses RLS, or that app.tenant_id / app.workspace_id are bound. Nothing "
                    "has been activated."
                )

            # Catch-up: anything ingested after the set was created. Reading
            # again from the start of the keyset is cheap relative to the
            # embedding and is the only way to notice a chunk whose id sorts
            # before the cursor.
            caught_up = await self._catch_up(
                binding=binding,
                embedder=embedder,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                batch_size=batch_size,
            )
            if caught_up:
                embedded += caught_up
                emit(f"{workspace_id}: caught up {caught_up} chunk(s) ingested during the run")

            indexed = await self._dense.ensure_set_index(binding)
            await self._sets.mark(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                set_id=binding.set_id,
                status="ready",
                vector_count=embedded,
                chunk_count=expected,
            )
            await self._record(
                job,
                stage="reindex.ready",
                message=f"{embedded} vector(s)",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                payload={"set_id": binding.set_id, "indexed": indexed},
            )

            retired: str | None = None
            if activate:
                if not indexed:
                    raise ReindexError(
                        f"embedding set {binding.set_id} is built but its ANN index could not be "
                        "created by this role, so activating it would serve searches from a "
                        "sequential scan. Run the CREATE INDEX printed above as the table owner, "
                        f"then `flycanon reindex --activate {binding.set_id}`."
                    )
                retired = await self._sets.activate(
                    tenant_id=tenant_id, workspace_id=workspace_id, set_id=binding.set_id
                )
                emit(f"{workspace_id}: activated {binding.set_id} (was {retired or 'none'})")
            await self._finish(job, tenant_id=tenant_id, workspace_id=workspace_id)

        return WorkspaceOutcome(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            set_id=binding.set_id,
            embedded=embedded,
            activated=activate,
            retired_set_id=retired,
            indexed=indexed,
            elapsed_s=time.perf_counter() - started,
            status="ready",
        )

    # ------------------------------------------------------------------
    # The pieces of a run
    # ------------------------------------------------------------------

    async def _open(
        self, *, workspace: WorkspacePlan, plan: ReindexPlan, resume_set_id: str | None
    ) -> tuple[EmbeddingSetBinding, IngestJobRow, dict[str, Any]]:
        """Resume an existing set, or create one, and return its job + cursor."""
        tenant_id, workspace_id = workspace.tenant_id, workspace.workspace_id
        if resume_set_id:
            existing = await self._sets.list_for(tenant_id=tenant_id, workspace_id=workspace_id)
            row = next((r for r in existing if r.id == resume_set_id), None)
            if row is None:
                raise ReindexError(
                    f"embedding set {resume_set_id} does not exist in workspace {workspace_id}"
                )
            binding = EmbeddingSetBinding.of(row)
            job = await self._job_for(set_id=resume_set_id, tenant_id=tenant_id, workspace_id=workspace_id)
            if job is None:
                raise ReindexError(
                    f"embedding set {resume_set_id} has no reindex job to resume; "
                    "start a fresh run without --resume"
                )
            cursor = dict((job.metadata_json or {}).get("cursor") or {})
            return binding, job, cursor

        row = await self._sets.create(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider=plan.provider,
            model=plan.model,
            dimensions=plan.dimensions,
            status="building",
            chunk_count=workspace.chunk_count,
            created_by="flycanon reindex",
        )
        job = IngestJobRow(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            status="running",
            kind="reindex",
            metadata_json={
                "set_id": row.id,
                "target": f"{plan.provider}:{plan.model}",
                "dimensions": plan.dimensions,
                "cursor": {
                    "set_id": row.id,
                    "last_chunk_id": None,
                    "done": 0,
                    "total": workspace.chunk_count,
                },
            },
        )
        await self._jobs.add(job)
        await self._record(
            job,
            stage="reindex.started",
            message=f"{workspace.chunk_count} chunk(s)",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            payload={"set_id": row.id, "target": f"{plan.provider}:{plan.model}"},
        )
        return EmbeddingSetBinding.of(row), job, {}

    async def _job_for(self, *, set_id: str, tenant_id: str, workspace_id: str) -> IngestJobRow | None:
        jobs = await self._jobs.list_jobs(tenant_id=tenant_id, workspace_id=workspace_id, limit=200)
        for job in jobs:
            if job.kind == "reindex" and (job.metadata_json or {}).get("set_id") == set_id:
                return job
        return None

    async def _embed_batch(
        self,
        batch: list[Any],
        *,
        binding: EmbeddingSetBinding,
        embedder: Any,
        tenant_id: str,
        workspace_id: str,
    ) -> int:
        """Embed one batch and write it into the target set.

        The vectors and the chunks' ``embedding_model`` stamp are written
        before the cursor advances, so a crash between the two re-reads a
        batch it has already written -- which ``ON CONFLICT (set_id, id)``
        makes a rewrite of identical rows rather than a duplicate.
        """
        vectors = await embedder.embed([chunk.content for chunk in batch])
        documents = [
            VectorDocument(
                id=chunk.id,
                text=chunk.content,
                embedding=list(vector),
                metadata={
                    "source_id": chunk.source_id,
                    "doc_id": chunk.source_id,
                    "section_path": chunk.section_path or "",
                    "page": str(chunk.page) if chunk.page is not None else "",
                },
            )
            for chunk, vector in zip(batch, vectors, strict=True)
        ]
        with bind_embedding_set(binding):
            await self._vector_store.upsert(documents, tenant_id=tenant_id, workspace_id=workspace_id)
        await self._chunks.stamp_embedding_model(
            chunk_ids=[chunk.id for chunk in batch], embedding_model=binding.embedding_model
        )
        return len(documents)

    async def _catch_up(
        self,
        *,
        binding: EmbeddingSetBinding,
        embedder: Any,
        tenant_id: str,
        workspace_id: str,
        batch_size: int,
    ) -> int:
        """Embed anything still carrying another model's stamp.

        ``embedding_model`` is the discriminator: a chunk ingested while the
        run was walking the corpus was stamped with the ACTIVE set's model, so
        it is exactly the set of rows this pass has to pick up.
        """
        caught = 0
        last_id: str | None = None
        while True:
            batch = await self._chunks.list_for_workspace(
                tenant_id=tenant_id, workspace_id=workspace_id, after_id=last_id, limit=batch_size
            )
            if not batch:
                return caught
            last_id = batch[-1].id
            stale = [c for c in batch if c.embedding_model != binding.embedding_model]
            if stale:
                caught += await self._embed_batch(
                    stale,
                    binding=binding,
                    embedder=embedder,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )

    async def _advance(
        self,
        job: IngestJobRow,
        *,
        set_id: str,
        last_chunk_id: str,
        done: int,
        total: int,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        job.metadata_json = {
            **(job.metadata_json or {}),
            "cursor": {"set_id": set_id, "last_chunk_id": last_chunk_id, "done": done, "total": total},
        }
        await self._jobs.update(job)
        await self._record(
            job,
            stage="reindex.batch",
            message=f"{done}/{total}",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            payload={"set_id": set_id, "done": done, "total": total},
        )

    async def _record(
        self,
        job: IngestJobRow,
        *,
        stage: str,
        message: str,
        tenant_id: str,
        workspace_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._jobs.append_event(
            job_id=job.id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            stage=stage,
            message=message,
            payload=payload,
        )

    async def _finish(self, job: IngestJobRow, *, tenant_id: str, workspace_id: str) -> None:
        """Close the job row.

        Not ``IngestJobRepository.mark_succeeded``: that one demands a
        ``source_id`` and is guarded by the ingest worker's claim lease, and a
        reindex job has neither a source nor a competing claimant.
        """
        job.status = "succeeded"
        job.finished_at = datetime.now(UTC)
        await self._jobs.update(job)
        await self._record(
            job,
            stage="reindex.finished",
            message="done",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    # ------------------------------------------------------------------
    # The switch, and its inverse
    # ------------------------------------------------------------------

    async def activate(self, *, tenant_id: str, workspace_id: str, set_id: str) -> str | None:
        with ScopedContext(tenant_id, workspace_id):
            return await self._sets.activate(tenant_id=tenant_id, workspace_id=workspace_id, set_id=set_id)

    async def rollback(self, *, tenant_id: str, workspace_id: str) -> str:
        with ScopedContext(tenant_id, workspace_id):
            return await self._sets.rollback(tenant_id=tenant_id, workspace_id=workspace_id)

    async def drop_set(self, *, tenant_id: str, workspace_id: str, set_id: str) -> int:
        """Delete a set's vectors, its index and its row. Refuses the active one."""
        from fireflyframework_agentic.vectorstores.scoped import scope_namespace

        with ScopedContext(tenant_id, workspace_id):
            rows = await self._sets.list_for(tenant_id=tenant_id, workspace_id=workspace_id)
            row = next((r for r in rows if r.id == set_id), None)
            if row is None:
                raise ReindexError(f"embedding set {set_id} does not exist in workspace {workspace_id}")
            if row.status == "active":
                raise EmbeddingSetError(
                    f"embedding set {set_id} is the one answering searches for {workspace_id}. "
                    "Activate another set first -- dropping this one would leave the workspace "
                    "with no dense retrieval at all."
                )
            deleted = await self._dense.drop_set(set_id, namespace=scope_namespace(tenant_id, workspace_id))
            await self._sets.delete(tenant_id=tenant_id, workspace_id=workspace_id, set_id=set_id)
            return deleted

    async def list_sets(self, *, scopes: list[tuple[str, str]]) -> list[SetListing]:
        """Every set of every workspace in scope, with which one is active."""
        listing: list[SetListing] = []
        for tenant_id, workspace_id in scopes:
            with ScopedContext(tenant_id, workspace_id):
                active = await self._sets.active_binding(tenant_id=tenant_id, workspace_id=workspace_id)
                rows = await self._sets.list_for(tenant_id=tenant_id, workspace_id=workspace_id)
            for row in rows:
                listing.append(
                    SetListing(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        row=row,
                        is_active=active is not None and active.set_id == row.id,
                    )
                )
        return listing


class ScopedContext:
    """Bind ``(tenant_id, workspace_id)`` for the ORM's RLS GUC listener.

    A context manager rather than a decorator because the reindex crosses
    workspaces inside one process, and the ``after_begin`` listener reads the
    ContextVar at the moment each transaction opens -- so the binding has to
    cover every await inside a workspace's turn, not the call that started it.
    """

    def __init__(self, tenant_id: str, workspace_id: str) -> None:
        self._ctx = TenantContext(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor="flycanon-reindex",
            correlation_id=f"reindex-{tenant_id}-{workspace_id}",
        )
        self._token: Any = None

    def __enter__(self) -> TenantContext:
        self._token = set_tenant_context(self._ctx)
        return self._ctx

    def __exit__(self, *_exc: object) -> None:
        self._token.var.reset(self._token.token)
