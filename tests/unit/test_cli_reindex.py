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

"""The ``flycanon reindex`` surface: parsing, confirmation, and the modes.

``cmd_reindex`` itself boots pyfly and belongs to the integration suite; what
is tested here is everything an operator can get wrong from the shell before
a single row is read.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pytest

from flycanon.cli import _confirmed, _dispatch, build_parser


def _args(*argv: str):
    return build_parser().parse_args(["reindex", *argv])


class TestParser:
    def test_reindex_is_a_subcommand_next_to_serve_worker_and_migrate(self) -> None:
        parser = build_parser()
        for command in ("serve", "migrate", "reindex"):
            assert parser.parse_args([command]).cmd == command

    def test_the_run_form_parses(self) -> None:
        args = _args(
            "--to", "azure:my-deploy", "--dimensions", "3072", "--tenant", "t-1", "--workspace", "w-1"
        )
        assert (args.to, args.dimensions, args.tenant, args.workspace) == (
            "azure:my-deploy",
            3072,
            "t-1",
            "w-1",
        )
        assert args.batch_size == 256
        assert args.no_activate is False

    def test_the_management_modes_parse(self) -> None:
        assert _args("--list", "--all").list is True
        assert _args("--activate", "es-1", "--tenant", "t", "--workspace", "w").activate == "es-1"
        assert _args("--rollback", "--tenant", "t", "--workspace", "w").rollback is True
        assert _args("--drop-set", "es-1", "--tenant", "t", "--workspace", "w").drop_set == "es-1"


class TestConfirmation:
    def test_yes_skips_the_prompt(self) -> None:
        assert _confirmed(True) is True

    def test_a_non_tty_without_yes_refuses(self, monkeypatch, capsys) -> None:
        """Assuming yes on a pipe is how an operator discovers a re-embed of
        every tenant after it has started."""
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert _confirmed(False) is False
        assert "refusing to reindex without --yes" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "answer,expected", [("y", True), ("yes", True), ("", False), ("n", False), ("maybe", False)]
    )
    def test_an_interactive_answer_is_honoured(self, monkeypatch, answer: str, expected: bool) -> None:
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr("sys.stdin", _Tty(""))
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)
        assert _confirmed(False) is expected


@dataclass
class _Row:
    id: str
    provider: str
    model: str
    dimensions: int
    status: str
    vector_count: int
    index_name: str | None


@dataclass
class _Entry:
    tenant_id: str
    workspace_id: str
    row: _Row
    is_active: bool


class _Plan:
    chunk_count = 9

    def render(self) -> str:
        return "reindex plan: 1 workspace(s), 9 chunk(s)"


class _Service:
    """Records what the dispatcher asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def resolve_scope(self, **kwargs: Any) -> list[tuple[str, str]]:
        self.calls.append(("resolve_scope", kwargs))
        return [("t-1", "w-1")]

    async def list_sets(self, **kwargs: Any) -> list[_Entry]:
        self.calls.append(("list_sets", kwargs))
        return [
            _Entry("t-1", "w-1", _Row("es-old", "ollama", "nomic", 768, "retired", 9, "ix_old"), False),
            _Entry("t-1", "w-1", _Row("es-new", "azure", "dep", 3072, "active", 9, "ix_new"), True),
        ]

    async def activate(self, **kwargs: Any) -> str:
        self.calls.append(("activate", kwargs))
        return "es-old"

    async def rollback(self, **kwargs: Any) -> str:
        self.calls.append(("rollback", kwargs))
        return "es-old"

    async def drop_set(self, **kwargs: Any) -> int:
        self.calls.append(("drop_set", kwargs))
        return 9

    async def plan(self, **kwargs: Any) -> _Plan:
        self.calls.append(("plan", kwargs))
        return _Plan()

    async def run(self, _plan: _Plan, **kwargs: Any) -> list[Any]:
        self.calls.append(("run", kwargs))
        return []


def _split(value: str) -> tuple[str, str]:
    provider, _, model = value.partition(":")
    return provider, model


async def _run(args, service: _Service) -> int:
    from flycanon.config import CanonSettings

    return await _dispatch(args, service=service, settings=CanonSettings(), split=_split)


class TestDispatch:
    async def test_list_marks_the_set_that_answers_searches(self, capsys) -> None:
        service = _Service()
        assert await _run(_args("--list", "--all"), service) == 0
        out = capsys.readouterr().out
        assert "* w-1  es-new  azure:dep @3072" in out
        assert "  w-1  es-old  ollama:nomic @768" in out
        assert "* = the set answering this workspace's searches" in out

    async def test_activate_reports_what_it_retired(self, capsys) -> None:
        service = _Service()
        args = _args("--activate", "es-new", "--tenant", "t-1", "--workspace", "w-1")
        assert await _run(args, service) == 0
        assert "w-1 now serves es-new (retired es-old)" in capsys.readouterr().out

    async def test_rollback_names_the_set_it_restored(self, capsys) -> None:
        service = _Service()
        args = _args("--rollback", "--tenant", "t-1", "--workspace", "w-1")
        assert await _run(args, service) == 0
        assert "rolled back to es-old" in capsys.readouterr().out

    async def test_drop_set_reports_the_rows_it_removed(self, capsys) -> None:
        service = _Service()
        args = _args("--drop-set", "es-old", "--tenant", "t-1", "--workspace", "w-1")
        assert await _run(args, service) == 0
        assert "dropped es-old: 9 vector(s)" in capsys.readouterr().out

    async def test_a_run_without_a_target_is_refused(self, capsys) -> None:
        service = _Service()
        assert await _run(_args("--all"), service) == 2
        assert "--to <provider>:<model> is required" in capsys.readouterr().err

    async def test_estimate_only_prints_the_plan_and_writes_nothing(self, capsys) -> None:
        service = _Service()
        args = _args("--to", "azure:dep", "--dimensions", "3072", "--all", "--estimate-only")
        assert await _run(args, service) == 0
        assert "reindex plan" in capsys.readouterr().out
        assert [name for name, _ in service.calls] == ["resolve_scope", "plan"]

    async def test_a_declined_confirmation_writes_nothing(self, monkeypatch, capsys) -> None:
        service = _Service()
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        args = _args("--to", "azure:dep", "--dimensions", "3072", "--all")
        assert await _run(args, service) == 1
        assert "aborted: nothing has been written" in capsys.readouterr().err
        assert "run" not in [name for name, _ in service.calls]

    async def test_yes_runs_with_the_parsed_knobs(self) -> None:
        service = _Service()
        args = _args(
            "--to",
            "azure:dep",
            "--dimensions",
            "3072",
            "--all",
            "--yes",
            "--batch-size",
            "64",
            "--no-activate",
        )
        assert await _run(args, service) == 0
        run = dict(service.calls)["run"]
        assert run["batch_size"] == 64
        assert run["activate"] is False

    async def test_the_default_width_is_the_process_default(self) -> None:
        """``--dimensions`` is optional: a provider swap at the same width is
        the common case and should not need the number restated."""
        service = _Service()
        args = _args("--to", "azure:dep", "--all", "--yes")
        await _run(args, service)
        assert dict(service.calls)["plan"]["dimensions"] == 1536
