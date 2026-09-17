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


def cmd_serve(_: argparse.Namespace) -> int:
    """Boot the PyFly application and serve the FastAPI app via uvicorn."""
    import uvicorn

    settings = get_settings()
    # pyfly v26.09 writes its ``server_started`` line from the
    # ``_PYFLY_SERVER_*`` variables that ``pyfly run`` exports; we start
    # uvicorn ourselves, so without these the boot log claimed
    # ``port=8080`` while the socket was on ``settings.port``. Export the
    # same contract so the log tells the truth an operator will act on.
    os.environ.setdefault("_PYFLY_SERVER_TYPE", "uvicorn")
    os.environ.setdefault("_PYFLY_SERVER_HOST", "0.0.0.0")
    os.environ.setdefault("_PYFLY_SERVER_PORT", str(settings.port))
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


def cmd_worker(_: argparse.Namespace) -> int:
    """Boot pyfly, resolve :class:`IngestWorker`, run forever."""
    ensure_worker_eda_group()

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(get_settings().log_level)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
