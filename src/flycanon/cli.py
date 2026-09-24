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

"""CLI entry point for flycanon.

Subcommands:

* ``flycanon serve``    -- run the FastAPI server on the configured port.
* ``flycanon worker``   -- run the EDA worker that consumes the
                           ``flycanon.ingest`` topic (async source
                           processing). Registered when the worker
                           module is on the path; the subcommand is
                           silently dropped from the parser otherwise so
                           a bare bootstrap install still parses.
* ``flycanon migrate``  -- run ``alembic upgrade head`` against the DB.
* ``flycanon reindex``  -- re-embed a corpus into a new embedding set and
                           switch to it atomically, plus ``--list`` /
                           ``--activate`` / ``--rollback`` / ``--drop-set``.
                           Changing the embedder is this command, never a
                           fresh database.

``serve`` lets uvicorn import ``flycanon.main:app`` (pyfly drives the
lifecycle there). The worker boots a minimal :class:`PyFlyApplication`
and pulls its concrete worker class out of the DI container; it never
constructs the worker itself, so the container owns every dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import sys

from flycanon.config import get_settings

logger = logging.getLogger("flycanon.cli")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


#: What the worker process exports for pyfly's ``server_started`` line.
#: There is no HTTP listener in that process, and pyfly casts the port
#: to ``int`` and logs unconditionally, so the honest values are a
#: server type nothing resolves a version for, a host that is not an
#: address, and port 0 ("no socket").
WORKER_SERVER_CONTRACT: dict[str, str] = {
    "_PYFLY_SERVER_TYPE": "none",
    "_PYFLY_SERVER_HOST": "-",
    "_PYFLY_SERVER_PORT": "0",
}


def export_server_contract(*, server_type: str, host: str, port: int | str) -> None:
    """Tell pyfly what this process listens on (or that it does not).

    pyfly v26.09 writes its ``server_started`` boot line from the
    ``_PYFLY_SERVER_*`` variables that ``pyfly run`` exports, and writes
    it in EVERY process that boots a :class:`PyFlyApplication`, with
    ``0.0.0.0:8080`` as the fallback. Both flycanon entry points start
    pyfly themselves: ``serve`` runs uvicorn on ``FLYCANON_PORT`` and
    ``worker`` runs no server at all. Without this export the API's
    log claimed ``port=8080`` while the socket was on 8500, and -- the
    skeptic's finding -- the worker's log claimed the same listener
    while it held no socket, which is the kind of line an operator
    reads during an incident and acts on. ``setdefault`` keeps an
    explicit environment (a supervisor that knows better) in charge.
    """
    os.environ.setdefault("_PYFLY_SERVER_TYPE", server_type)
    os.environ.setdefault("_PYFLY_SERVER_HOST", host)
    os.environ.setdefault("_PYFLY_SERVER_PORT", str(port))


def cmd_serve(_: argparse.Namespace) -> int:
    """Boot the PyFly application and serve the FastAPI app via uvicorn."""
    import uvicorn

    settings = get_settings()
    export_server_contract(server_type="uvicorn", host="0.0.0.0", port=settings.port)
    uvicorn.run(
        "flycanon.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


#: Consumer group the worker process drains. Distinct from the API's
#: default (``flycanon-api`` in pyfly.yaml) on purpose -- see the
#: ``pyfly.eda.group`` comment there for the outage this prevents.
WORKER_EDA_GROUP = "flycanon-workers"


def ensure_worker_eda_group() -> str:
    """Default ``FLYCANON_EDA_GROUP`` for the worker process; return the value in force.

    Must run BEFORE :class:`PyFlyApplication` reads ``pyfly.yaml``,
    because that is where ``${FLYCANON_EDA_GROUP:...}`` is interpolated.
    An explicit environment value always wins (an operator scaling
    workers keeps them on one shared group deliberately).
    """
    return os.environ.setdefault("FLYCANON_EDA_GROUP", WORKER_EDA_GROUP)


def ensure_worker_server_contract() -> None:
    """Export the "no listener" server contract for the worker process.

    Must run BEFORE :class:`PyFlyApplication.startup`, which is where
    pyfly reads the variables and logs ``server_started``.
    """
    export_server_contract(
        server_type=WORKER_SERVER_CONTRACT["_PYFLY_SERVER_TYPE"],
        host=WORKER_SERVER_CONTRACT["_PYFLY_SERVER_HOST"],
        port=WORKER_SERVER_CONTRACT["_PYFLY_SERVER_PORT"],
    )


def cmd_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, resolve :class:`IngestWorker`, run forever."""
    ensure_worker_eda_group()
    ensure_worker_server_contract()
    logger.info(
        "flycanon worker: no HTTP listener in this process "
        "(pyfly's server_started line reports server=none port=0)"
    )

    async def _run() -> None:
        from pyfly.core import PyFlyApplication
        from pyfly.eda import EventPublisher

        worker_mod = importlib.import_module("flycanon.core.services.workers.ingest_worker")
        ingestion_mod = importlib.import_module("flycanon.core.services.ingestion")
        repo_mod = importlib.import_module("flycanon.models.repositories")
        async_ingest_mod = importlib.import_module("flycanon.core.services.sources.async_ingest_service")

        from flycanon.app import CanonApplication
        from flycanon.config import CanonSettings

        pyfly_app = PyFlyApplication(CanonApplication)
        await pyfly_app.startup()
        try:
            container = pyfly_app.context.container
            try:
                async_ingest = container.resolve(async_ingest_mod.AsyncIngestService)
            except Exception:
                async_ingest = None
            worker = worker_mod.IngestWorker(
                ingestion=container.resolve(ingestion_mod.IngestionService),
                repository=container.resolve(repo_mod.SourceRepository),
                event_publisher=container.resolve(EventPublisher),
                settings=container.resolve(CanonSettings),
                async_ingest=async_ingest,
            )
            await worker.run_forever()
        finally:
            await pyfly_app.shutdown()

    asyncio.run(_run())
    return 0


#: What a reindex batch costs in one handler invocation, and why the batches
#: run here rather than on the worker: see ``ReindexService``'s docstring.
REINDEX_DEFAULT_BATCH_SIZE = 256


def cmd_reindex(args: argparse.Namespace) -> int:
    """Re-embed a corpus into a new embedding set, or manage the sets.

    Boots pyfly so every dependency comes out of the container -- the same
    discipline ``worker`` follows -- and then drives
    :class:`ReindexService`. Scope selection is mandatory and explicit: there
    is no bare ``flycanon reindex`` that silently means ``--all``.
    """

    async def _run() -> int:
        from pyfly.core import PyFlyApplication

        from flycanon.app import CanonApplication
        from flycanon.core.services.embeddings import EmbeddingRegistry, EmbeddingSetService
        from flycanon.core.services.embeddings.embedding_sets import split_embedding_model
        from flycanon.core.services.embeddings.reindex_service import ReindexError, ReindexService
        from flycanon.core.services.retrieval.corpus_factory import CorpusContext
        from flycanon.models.repositories import ChunkRepository, IngestJobRepository

        ensure_worker_server_contract()
        pyfly_app = PyFlyApplication(CanonApplication)
        await pyfly_app.startup()
        try:
            container = pyfly_app.context.container
            settings = get_settings()
            context = container.resolve(CorpusContext)
            logger.info(
                "reindex runs on FLYCANON_DATABASE_URL. It reads across workspaces and creates "
                "one ANN index per set, so point it at a role that bypasses RLS and owns "
                "canon_chunk_vectors (the migrate role). A role that cannot see a workspace's "
                "chunks fails the run rather than activating an empty set."
            )
            service = ReindexService(
                chunks=container.resolve(ChunkRepository),
                jobs=container.resolve(IngestJobRepository),
                sets=container.resolve(EmbeddingSetService),
                registry=container.resolve(EmbeddingRegistry),
                vector_store=context.vector_store,
                dense_backend=context.dense_backend,
                settings=settings,
            )
            try:
                return await _dispatch(args, service=service, settings=settings, split=split_embedding_model)
            except (ReindexError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        finally:
            await pyfly_app.shutdown()

    return asyncio.run(_run())


async def _dispatch(args: argparse.Namespace, *, service, settings, split) -> int:  # noqa: ANN001
    """Route one invocation to a mode. Exactly one mode per call."""
    if args.list:
        scopes = await service.resolve_scope(
            workspace_id=args.workspace, tenant_id=args.tenant, everything=args.all
        )
        for entry in await service.list_sets(scopes=scopes):
            row = entry.row
            marker = "*" if entry.is_active else " "
            print(
                f"{marker} {entry.workspace_id}  {row.id}  {row.provider}:{row.model} @{row.dimensions}"
                f"  {row.status:<9} vectors={row.vector_count}  index={row.index_name or '-'}"
            )
        print("\n* = the set answering this workspace's searches")
        return 0

    if args.activate:
        retired = await service.activate(
            tenant_id=args.tenant, workspace_id=args.workspace, set_id=args.activate
        )
        print(f"{args.workspace} now serves {args.activate} (retired {retired or 'nothing'})")
        return 0

    if args.rollback:
        restored = await service.rollback(tenant_id=args.tenant, workspace_id=args.workspace)
        print(f"{args.workspace} rolled back to {restored}")
        return 0

    if args.drop_set:
        deleted = await service.drop_set(
            tenant_id=args.tenant, workspace_id=args.workspace, set_id=args.drop_set
        )
        print(f"dropped {args.drop_set}: {deleted} vector(s) and its index removed")
        return 0

    if not args.to:
        print("error: --to <provider>:<model> is required to run a reindex", file=sys.stderr)
        return 2
    provider, model = split(args.to)
    dimensions = args.dimensions or settings.embedding_dimensions
    scopes = await service.resolve_scope(
        workspace_id=args.workspace, tenant_id=args.tenant, everything=args.all
    )
    plan = await service.plan(scopes=scopes, provider=provider, model=model, dimensions=dimensions)
    print(plan.render())
    if args.estimate_only:
        return 0
    if not _confirmed(args.yes):
        print("aborted: nothing has been written", file=sys.stderr)
        return 1

    outcomes = await service.run(
        plan,
        batch_size=args.batch_size,
        activate=not args.no_activate,
        resume_set_id=args.resume,
        on_progress=lambda line: print(line, flush=True),
    )
    throttled = [o for o in outcomes if o.status == "throttled"]
    for outcome in outcomes:
        print(
            f"{outcome.workspace_id}: {outcome.embedded} vector(s) in {outcome.set_id} "
            f"({outcome.status}, {outcome.elapsed_s:.1f}s)"
            + (f", retired {outcome.retired_set_id}" if outcome.retired_set_id else "")
        )
    if throttled:
        print(
            "\nsome workspaces stopped on a provider rate limit. Their cursors are saved; "
            "resume with --resume <set-id>.",
            file=sys.stderr,
        )
        return 3
    return 0


def _confirmed(yes: bool) -> bool:
    """``--yes``, or an interactive y. A non-tty without ``--yes`` refuses.

    Assuming yes on a pipe is how an operator discovers a re-embed of every
    tenant after it has started.
    """
    if yes:
        return True
    if not sys.stdin.isatty():
        print(
            "refusing to reindex without --yes: stdin is not a terminal, so there is nobody to ask.",
            file=sys.stderr,
        )
        return False
    return input("Proceed? [y/N] ").strip().lower() in ("y", "yes")


def cmd_migrate(_: argparse.Namespace) -> int:
    """Apply Alembic migrations."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    here = os.path.dirname(os.path.abspath(__file__))
    cfg = AlembicConfig(os.path.join(here, "..", "..", "alembic.ini"))
    settings = get_settings()
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    return 0


def _worker_module_available() -> bool:
    """Return ``True`` when the worker module ships with this install.

    Lets the bootstrap skeleton (which doesn't carry the worker yet)
    register only the subcommands it can actually run. Later commits
    land the worker package and the subcommand becomes available
    without changes to this file.
    """
    try:
        return importlib.util.find_spec("flycanon.core.services.workers.ingest_worker") is not None
    except (ImportError, ValueError):
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flycanon",
        description="flycanon -- Operational Knowledge Repository service",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub_serve = sub.add_parser("serve", help="Run the FastAPI server")
    sub_serve.set_defaults(func=cmd_serve)

    if _worker_module_available():
        sub_worker = sub.add_parser("worker", help="Run the ingestion EDA worker")
        sub_worker.set_defaults(func=cmd_worker)

    sub_migrate = sub.add_parser("migrate", help="Apply database migrations")
    sub_migrate.set_defaults(func=cmd_migrate)

    sub_reindex = sub.add_parser(
        "reindex",
        help="Re-embed a corpus into a new embedding set, and manage the sets",
        description=(
            "Re-embed one workspace, one tenant or every workspace into a new embedding set, "
            "then switch to it atomically. The old set serves searches for the whole run and "
            "stays available for --rollback until it is --drop-set."
        ),
    )
    sub_reindex.add_argument(
        "--to", help="target embedder, ``<provider>:<model>`` (on Azure the model is the DEPLOYMENT name)"
    )
    sub_reindex.add_argument(
        "--dimensions", type=int, help="target width; defaults to FLYCANON_EMBEDDING_DIMENSIONS"
    )
    sub_reindex.add_argument("--workspace", help="workspace id (needs --tenant)")
    sub_reindex.add_argument("--tenant", help="tenant id")
    sub_reindex.add_argument("--all", action="store_true", help="every workspace that holds chunks")
    sub_reindex.add_argument(
        "--estimate-only", action="store_true", help="print the plan and the cost, write nothing"
    )
    sub_reindex.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sub_reindex.add_argument("--resume", metavar="SET_ID", help="continue a run from its saved cursor")
    sub_reindex.add_argument(
        "--batch-size",
        type=int,
        default=REINDEX_DEFAULT_BATCH_SIZE,
        help=f"chunks per batch (default {REINDEX_DEFAULT_BATCH_SIZE})",
    )
    sub_reindex.add_argument(
        "--no-activate", action="store_true", help="build the set but leave the workspace on its current one"
    )
    sub_reindex.add_argument("--list", action="store_true", help="list the embedding sets in scope")
    sub_reindex.add_argument("--activate", metavar="SET_ID", help="switch a workspace to an existing set")
    sub_reindex.add_argument(
        "--rollback", action="store_true", help="switch a workspace back to its previously active set"
    )
    sub_reindex.add_argument(
        "--drop-set", metavar="SET_ID", help="delete a retired set's vectors, index and row"
    )
    sub_reindex.set_defaults(func=cmd_reindex)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(get_settings().log_level)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
