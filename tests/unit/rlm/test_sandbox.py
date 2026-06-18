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

"""Tests for the subprocess sandbox: real-child integration + unit-level codec.

The integration tests spawn the actual :mod:`runner` child via
:class:`SandboxExecutor` and drive it with **fake** capability handlers -- no
network, no LLM, no API key. They assert the security properties (scrubbed env,
file-write block, timeout kill) and the capability round-trips (docs/llm/final).
The remaining tests exercise the frame codec and the parent's rejection of
malformed/unknown child frames directly.
"""

from __future__ import annotations

import dataclasses
import io
import os
import resource
import socket
import subprocess
import sys
import time

import pytest

from flycanon.core.services.query.rlm.sandbox import _proto, runner
from flycanon.core.services.query.rlm.sandbox.executor import (
    BlockResult,
    SandboxExecutor,
)

# A sentinel secret set in the parent's environment before start(); the child
# must NOT be able to see it (scrubbed env).
SECRET_ENV = "FLYCANON_TEST_SECRET"
SECRET_VALUE = "do-not-leak-this-token"


def _fake_handlers(**overrides):
    """A complete set of inert capability handlers, with optional overrides."""
    handlers = {
        "docs_keys": lambda: ["A", "B"],
        "docs_getitem": lambda fid: f"text-of-{fid}",
        "docs_pages": lambda fid: [f"{fid}-p0", f"{fid}-p1"],
        "docs_npages": lambda fid: 2,
        "docs_contains": lambda fid: fid in ("A", "B"),
        "llm": lambda prompt: f"llm-said:{prompt}",
        "rlm": lambda question, text: f"rlm-said:{question}",
    }
    handlers.update(overrides)
    return handlers


@pytest.fixture
def make_executor():
    """Factory yielding started executors, all closed at teardown."""
    created: list[SandboxExecutor] = []

    def _make(**overrides):
        timeout = overrides.pop("timeout", 15.0)
        text = overrides.pop("text", None)
        ex = SandboxExecutor(timeout=timeout, text=text, **_fake_handlers(**overrides))
        ex.start()
        created.append(ex)
        return ex

    yield _make
    for ex in created:
        ex.close()


# ---------------------------------------------------------------------------
# capability round-trips (real child, fake handlers)
# ---------------------------------------------------------------------------
def test_exec_print_returns_stdout(make_executor):
    ex = make_executor()
    result = ex.run_block("print('hello from child')")
    assert result.kind == "stdout"
    assert "hello from child" in result.stdout


def test_persistent_namespace_across_blocks(make_executor):
    ex = make_executor()
    first = ex.run_block("x = 41")
    assert first.kind == "stdout"
    second = ex.run_block("print(x + 1)")
    assert second.kind == "stdout"
    assert "42" in second.stdout


def test_docs_getitem_serviced_by_handler(make_executor):
    seen: list[str] = []

    def getitem(fid: str) -> str:
        seen.append(fid)
        return f"BODY[{fid}]"

    ex = make_executor(docs_getitem=getitem)
    result = ex.run_block("print(docs['ACME_2020'])")
    assert result.kind == "stdout"
    # the handler was called with the right fid, and its value reached the code
    assert seen == ["ACME_2020"]
    assert "BODY[ACME_2020]" in result.stdout


def test_docs_keys_and_pages_serviced(make_executor):
    pages_seen: list[str] = []

    def pages(fid: str) -> list[str]:
        pages_seen.append(fid)
        return ["page-zero", "page-one"]

    ex = make_executor(docs_pages=pages)
    result = ex.run_block("print(docs.keys()); print(docs.pages('A')[1])")
    assert result.kind == "stdout"
    assert "['A', 'B']" in result.stdout
    assert pages_seen == ["A"]
    assert "page-one" in result.stdout


def test_docs_npages_and_contains_serviced(make_executor):
    ex = make_executor()
    result = ex.run_block("print(docs.npages('A')); print('A' in docs); print('Z' in docs)")
    assert result.kind == "stdout"
    assert "2" in result.stdout
    assert "True" in result.stdout
    assert "False" in result.stdout


def test_llm_serviced_by_handler(make_executor):
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return "extracted-value-7"

    ex = make_executor(llm=llm)
    result = ex.run_block("print(llm('pull the number from this chunk'))")
    assert result.kind == "stdout"
    assert prompts == ["pull the number from this chunk"]
    assert "extracted-value-7" in result.stdout


def test_rlm_serviced_by_handler(make_executor):
    calls: list[tuple[str, str]] = []

    def rlm(question: str, text: str) -> str:
        calls.append((question, text))
        return "nested-answer"

    ex = make_executor(rlm=rlm)
    result = ex.run_block("print(rlm('inner q', 'big text body'))")
    assert result.kind == "stdout"
    assert calls == [("inner q", "big text body")]
    assert "nested-answer" in result.stdout


def test_final_returns_final_blockresult(make_executor):
    ex = make_executor()
    result = ex.run_block("final('the answer', filings=['A', 'B'], pages=[0, 1], found=False)")
    assert result.kind == "final"
    assert result.final == {
        "answer": "the answer",
        "filings": ["A", "B"],
        "pages": [0, 1],
        "found": False,
    }


def test_final_defaults_found_true(make_executor):
    ex = make_executor()
    result = ex.run_block("final('yes', filings=['A'])")
    assert result.kind == "final"
    assert result.final["found"] is True
    assert result.final["pages"] is None


def test_text_mode_exposes_text_not_docs(make_executor):
    ex = make_executor(text="the secret figure is 99")
    out = ex.run_block("print(text.split()[-1])")
    assert out.kind == "stdout"
    assert "99" in out.stdout
    # in text mode there is no `docs` binding
    missing = ex.run_block("print(docs)")
    assert missing.kind == "error"
    assert "NameError" in missing.error


def test_raising_handler_is_reported_into_child_and_keeps_it_alive(make_executor):
    def boom(fid: str) -> str:
        raise KeyError(fid)

    ex = make_executor(docs_getitem=boom)
    # the handler raises -> the child sees a normal exception, surfaced as an
    # error frame (NOT a parent crash, NOT an orphaned child)
    result = ex.run_block("print(docs['MISSING'])")
    assert result.kind == "error"
    assert "docs_getitem failed" in result.error
    assert "KeyError" in result.error
    # the child is still alive: a follow-up block runs in the same namespace
    assert ex._proc is not None
    again = ex.run_block("print('still here')")
    assert again.kind == "stdout"
    assert "still here" in again.stdout


def test_handler_caught_inside_child_keeps_block_alive(make_executor):
    def boom(fid: str) -> str:
        raise KeyError(fid)

    ex = make_executor(docs_getitem=boom)
    # model code can catch the relayed failure and carry on within the block.
    # Exception classes are not in the safe builtins, so use a bare except.
    result = ex.run_block("try:\n    docs['X']\nexcept:\n    print('caught')")
    assert result.kind == "stdout"
    assert "caught" in result.stdout


def test_nonserializable_handler_value_does_not_crash_parent(make_executor):
    def weird() -> object:
        return object()  # not JSON-serialisable

    ex = make_executor(docs_keys=weird)
    result = ex.run_block("print(docs.keys())")
    # the non-JSON value makes the parent's result-encode raise TypeError; the
    # parent must report a terminal outcome and kill the child, never propagate
    # the crash (the child is dead -> terminated, not a recoverable error).
    assert result.kind == "terminated"
    assert "result write failed" in result.error
    assert ex._proc is None


# ---------------------------------------------------------------------------
# error / security properties
# ---------------------------------------------------------------------------
def test_exception_returns_error_with_traceback(make_executor):
    ex = make_executor()
    # int() on a non-number raises a real ValueError (exception classes are not
    # in the safe builtins, so we trigger one via an allowed builtin).
    result = ex.run_block("print(int('not-a-number'))")
    assert result.kind == "error"
    assert "ValueError" in result.error
    assert "Traceback" in result.error


def test_open_is_blocked(make_executor):
    ex = make_executor()
    result = ex.run_block("open('/tmp/should_not_exist', 'w')")
    assert result.kind == "error"
    # `open` is not in the safe builtins -> NameError before any file is touched
    assert "NameError" in result.error


def test_import_is_blocked(make_executor):
    ex = make_executor()
    result = ex.run_block("import os\nprint(os.getcwd())")
    assert result.kind == "error"
    assert "ImportError" in result.error or "NameError" in result.error


def test_timeout_kills_child(make_executor):
    ex = make_executor(timeout=1.0)
    result = ex.run_block("while True:\n    pass")
    assert result.kind == "terminated"
    assert "timed out" in result.error
    # after a kill the executor must refuse further work cleanly
    assert ex._proc is None


def test_child_env_is_scrubbed_of_secret(monkeypatch, make_executor):
    monkeypatch.setenv(SECRET_ENV, SECRET_VALUE)
    ex = make_executor()
    # The env the executor hands the child must omit the sentinel secret -- only
    # the minimal whitelist (PATH/LANG/PYTHONPATH...) is forwarded.
    env = ex._child_env(7)
    assert SECRET_ENV not in env
    assert SECRET_VALUE not in env.values()
    assert "PATH" in env  # whitelisted essentials still present
    # sanity: the child still runs end to end with the scrubbed env
    result = ex.run_block("print('alive')")
    assert result.kind == "stdout"
    assert "alive" in result.stdout


def test_apply_rlimits_sets_fsize_zero_cpu_and_as():
    """Defence in depth: the child clamps file-size to zero plus CPU/memory.

    Run in a forked process so the real (irreversible) ``setrlimit`` calls do
    not clamp the test runner itself. After ``_apply_rlimits`` the child cannot
    create or grow any file (RLIMIT_FSIZE soft limit == 0), which complements
    the absent ``open`` builtin.
    """
    pid = os.fork()
    if pid == 0:  # child: apply limits and report the soft caps via exit code
        runner._apply_rlimits()
        fsize_soft, _ = resource.getrlimit(resource.RLIMIT_FSIZE)
        cpu_soft, _ = resource.getrlimit(resource.RLIMIT_CPU)
        as_soft, _ = resource.getrlimit(resource.RLIMIT_AS)
        ok = fsize_soft == 0 and cpu_soft == runner._CPU_SECONDS and as_soft == runner._ADDRESS_SPACE
        os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def test_stdout_is_capped(make_executor):
    ex = make_executor()
    result = ex.run_block("print('x' * 10000)")
    assert result.kind == "stdout"
    # capped at ~4000 chars (the in-process _MAX_STDOUT)
    assert len(result.stdout) <= 4001  # 4000 chars (+ possible trailing newline trim)


def test_close_is_idempotent(make_executor):
    ex = make_executor()
    ex.run_block("print('once')")
    ex.close()
    ex.close()  # second close must not raise
    assert ex._proc is None


# ---------------------------------------------------------------------------
# frame codec (unit)
# ---------------------------------------------------------------------------
def test_codec_round_trip():
    frame = {"op": "exec", "code": "print(1)", "nested": {"a": [1, 2, 3]}}
    blob = _proto.encode(frame)
    assert _proto.decode(io.BytesIO(blob).read) == frame


def test_codec_round_trip_multiple_frames():
    a = {"op": "result", "value": "first"}
    b = {"op": "final", "answer": "second"}
    stream = io.BytesIO(_proto.encode(a) + _proto.encode(b))
    assert _proto.decode(stream.read) == a
    assert _proto.decode(stream.read) == b


def test_codec_clean_eof_raises_eoferror():
    with pytest.raises(EOFError):
        _proto.decode(io.BytesIO(b"").read)


def test_codec_truncated_frame_raises_protocolerror():
    blob = _proto.encode({"op": "exec", "code": "x"})
    truncated = blob[:-3]  # drop part of the payload
    with pytest.raises(_proto.ProtocolError):
        _proto.decode(io.BytesIO(truncated).read)


def test_codec_truncated_header_raises_protocolerror():
    with pytest.raises(_proto.ProtocolError):
        _proto.decode(io.BytesIO(b"\x00\x01").read)  # 2 of 4 header bytes


def test_codec_oversized_length_prefix_rejected():
    # announce a huge length without sending the bytes -> reject before reading
    header = (_proto.MAX_FRAME + 1).to_bytes(_proto.HEADER_LEN, "big")
    with pytest.raises(_proto.ProtocolError):
        _proto.decode(io.BytesIO(header).read)


def test_codec_non_json_payload_rejected():
    body = b"not json at all"
    framed = len(body).to_bytes(_proto.HEADER_LEN, "big") + body
    with pytest.raises(_proto.ProtocolError):
        _proto.decode(io.BytesIO(framed).read)


def test_codec_non_object_top_level_rejected():
    body = b"[1, 2, 3]"
    framed = len(body).to_bytes(_proto.HEADER_LEN, "big") + body
    with pytest.raises(_proto.ProtocolError):
        _proto.decode(io.BytesIO(framed).read)


def test_encode_rejects_oversized_frame():
    huge = {"op": "exec", "code": "x" * (_proto.MAX_FRAME + 10)}
    with pytest.raises(_proto.ProtocolError):
        _proto.encode(huge)


# ---------------------------------------------------------------------------
# parent rejects malformed / unknown child frames (no real child needed)
# ---------------------------------------------------------------------------
class _FakeSock:
    """A real-fd socket stand-in: a socketpair pre-loaded with canned bytes.

    The parent's ``_service_loop`` does ``select.select`` on the socket, so the
    fd must be genuinely readable -- a socketpair gives us that deterministically
    (independent of the test's stdin). Outgoing ``sendall`` is captured.
    """

    def __init__(self, incoming: bytes):
        self._a, self._b = socket.socketpair()
        if incoming:
            self._b.sendall(incoming)
        self._b.shutdown(socket.SHUT_WR)  # signal EOF after the canned bytes
        self.sent = bytearray()

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def settimeout(self, timeout: float | None) -> None:
        self._a.settimeout(timeout)

    def recv(self, size: int) -> bytes:
        return self._a.recv(size)

    def close(self) -> None:
        self._a.close()
        self._b.close()

    def fileno(self) -> int:
        return self._a.fileno()


class _FakeProc:
    """A subprocess stand-in: reports alive, swallows signals/waits."""

    def __init__(self):
        self.killed = False

    def poll(self):
        return None  # alive

    def send_signal(self, sig):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _parent_with(frame_bytes: bytes) -> SandboxExecutor:
    """A started-looking executor whose 'child' just emits ``frame_bytes``."""
    ex = SandboxExecutor(timeout=5.0, **_fake_handlers())
    ex._proc = _FakeProc()
    ex._parent_sock = _FakeSock(frame_bytes)
    return ex


def test_parent_rejects_unknown_op(monkeypatch):
    ex = _parent_with(_proto.encode({"op": "definitely-not-a-real-op"}))
    proc = ex._proc
    result = ex.run_block("noop")
    assert result.kind == "terminated"
    assert "unknown child op" in result.error
    assert proc.killed is True  # the child was killed on the bad frame


def test_parent_rejects_unknown_rpc_fn():
    ex = _parent_with(_proto.encode({"op": "rpc", "fn": "exfiltrate", "args": ["x"]}))
    proc = ex._proc
    result = ex.run_block("noop")
    assert result.kind == "terminated"
    assert "unknown rpc fn" in result.error
    assert proc.killed is True


def test_parent_rejects_bad_rpc_arity():
    ex = _parent_with(_proto.encode({"op": "rpc", "fn": "docs_getitem", "args": ["a", "b"]}))
    result = ex.run_block("noop")
    assert result.kind == "terminated"
    assert "bad args" in result.error


def test_parent_rejects_non_string_rpc_args():
    ex = _parent_with(_proto.encode({"op": "rpc", "fn": "docs_getitem", "args": [123]}))
    result = ex.run_block("noop")
    assert result.kind == "terminated"
    assert "non-string args" in result.error


def test_parent_rejects_malformed_frame():
    # a valid header announcing 5 bytes, but only 2 follow -> truncated
    bad = (5).to_bytes(_proto.HEADER_LEN, "big") + b"ab"
    ex = _parent_with(bad)
    proc = ex._proc
    result = ex.run_block("noop")
    assert result.kind == "terminated"
    assert "malformed child frame" in result.error
    assert proc.killed is True


def test_parent_times_out_on_partial_frame_child():
    # The child announces a 100-byte frame then stops sending the body. select
    # reports the socket readable (the header arrived), so the parent enters
    # _read_exact; without a per-recv deadline it would block forever. The
    # wall-clock timeout must still fire and kill the child.
    header = (100).to_bytes(_proto.HEADER_LEN, "big")
    a, b = socket.socketpair()
    b.sendall(header)  # header only; the rest of the frame never comes

    ex = SandboxExecutor(timeout=0.5, **_fake_handlers())
    ex._proc = _FakeProc()
    proc = ex._proc
    # use the live socketpair directly so recv() genuinely blocks until timeout
    ex._parent_sock = a

    start = time.monotonic()
    result = ex.run_block("noop")
    elapsed = time.monotonic() - start

    assert result.kind == "terminated"
    assert "timed out" in result.error
    assert proc.killed is True
    assert elapsed < 5.0  # bounded by the 0.5s timeout, not blocked forever
    b.close()


def test_parent_services_valid_rpc_then_returns_stdout():
    # child: docs_keys rpc, then a stdout frame -> parent answers rpc, returns stdout
    stream = _proto.encode({"op": "rpc", "fn": "docs_keys", "args": []})
    stream += _proto.encode({"op": "stdout", "text": "done"})
    keys_seen: list[bool] = []

    handlers = _fake_handlers(docs_keys=lambda: keys_seen.append(True) or ["A"])
    ex = SandboxExecutor(timeout=5.0, **handlers)
    ex._proc = _FakeProc()
    ex._parent_sock = _FakeSock(stream)

    result = ex.run_block("noop")
    assert result.kind == "stdout"
    assert result.stdout == "done"
    assert keys_seen == [True]
    # the parent replied to the rpc with a result frame
    reply = _proto.decode(io.BytesIO(bytes(ex._parent_sock.sent)).read)
    assert reply["op"] == "exec"  # first thing the parent sent was the exec command
    rest = bytes(ex._parent_sock.sent)
    # second frame the parent sent is the rpc result
    first_len = _proto.HEADER_LEN + int.from_bytes(rest[: _proto.HEADER_LEN], "big")
    second = _proto.decode(io.BytesIO(rest[first_len:]).read)
    assert second == {"op": "result", "value": ["A"]}


def test_child_runner_stays_import_light():
    """Importing the runner must not drag in httpx/config/agents/services.

    The child is spawned with a scrubbed env and must hold no secrets and do no
    network. Importing it through the query package previously pulled in
    AnswerService/SearchService (-> CanonSettings, agents, pydantic_ai, httpx);
    import it in a clean subprocess and assert none of that heavy stack loaded.
    """
    probe = (
        "import sys, importlib;"
        "importlib.import_module('flycanon.core.services.query.rlm.sandbox.runner');"
        "heavy = [m for m in ("
        "'httpx', 'pydantic_ai', 'flycanon.core.agents',"
        "'flycanon.core.services.query.answer_service',"
        "'flycanon.core.services.query.search_service',"
        "'flycanon.core.services.retrieval'"
        ") if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "", f"runner pulled in heavy modules: {out.stdout!r}"


def test_blockresult_is_frozen():
    r = BlockResult(kind="stdout", stdout="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.kind = "error"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# inactivity timeout (fix 1): a child making progress is never killed; a silent
# child still is.
# ---------------------------------------------------------------------------
def test_slow_but_progressing_block_does_not_time_out(make_executor):
    # The timeout is an INACTIVITY timeout, not a total-block budget. Here each
    # llm() RPC the parent services sleeps 0.2s -- shorter than the 0.5s timeout
    # -- but the child makes 5 of them, so the WHOLE block runs ~1.0s, well over
    # the timeout. Because the child keeps emitting frames (and the deadline
    # resets after each), it must NOT be killed: the block completes normally.
    calls: list[str] = []

    def slow_llm(prompt: str) -> str:
        time.sleep(0.2)
        calls.append(prompt)
        return f"ok:{prompt}"

    ex = make_executor(timeout=0.5, llm=slow_llm)
    start = time.monotonic()
    result = ex.run_block(
        "for i in range(5):\n    print(llm(str(i)))"
    )
    elapsed = time.monotonic() - start
    assert result.kind == "stdout", result.error
    # total wall-clock comfortably exceeds the 0.5s timeout, proving the timeout
    # measures silence, not total block time.
    assert elapsed > 0.5
    assert len(calls) == 5
    assert "ok:4" in result.stdout
    assert ex._proc is not None  # the child survived: still usable


def test_silent_child_times_out_to_terminated(make_executor):
    # A genuinely hung child (busy-loop, emits no frames) goes silent past the
    # inactivity window and is killed -> terminated within ~timeout.
    ex = make_executor(timeout=0.5)
    start = time.monotonic()
    result = ex.run_block("while True:\n    pass")
    elapsed = time.monotonic() - start
    assert result.kind == "terminated"
    assert "timed out" in result.error
    assert elapsed < 5.0  # bounded by the inactivity timeout, not blocked forever
    assert ex._proc is None


def test_child_death_mid_session_makes_next_run_block_terminated(make_executor):
    # A prior block kills the child (here: a runaway loop trips the inactivity
    # timeout -> _kill -> _proc=None). The NEXT run_block must return a
    # terminated result, NOT raise RuntimeError('sandbox not started').
    ex = make_executor(timeout=0.5)
    first = ex.run_block("while True:\n    pass")
    assert first.kind == "terminated"
    assert ex._proc is None
    # the executor is now dead; a follow-up block degrades cleanly.
    second = ex.run_block("print('should not run')")
    assert second.kind == "terminated"
    assert "not started" in second.error
