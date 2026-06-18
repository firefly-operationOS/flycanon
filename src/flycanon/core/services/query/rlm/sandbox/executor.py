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

"""The sandbox parent: drive an untrusted child REPL and service its requests.

:class:`SandboxExecutor` spawns the :mod:`runner` child with a **scrubbed**
environment (a minimal whitelist -- no API keys, cloud creds, DB/Redis URLs, or
``FLYCANON_*`` secrets) over a private socketpair, with ``close_fds`` so the
child inherits no other parent descriptors. ``run_block(code)`` runs one block
in the child and services its capability requests: when the child emits an
``rpc`` frame the parent validates the function name and argument types, calls
the caller-injected handler, and replies with a ``result`` frame; the child's
``docs``/``llm``/``rlm``/``final`` stubs thus reach the real document store and
model client that live *only* here.

Every child frame is treated as untrusted: parsed with :mod:`json` only (never
``pickle``/``eval``), bounded in size, and validated against fixed allowlists. A
malformed frame, an unknown op/fn, or a wall-clock timeout never crashes the
parent -- the child is killed and a controlled error :class:`BlockResult` is
returned.
"""

from __future__ import annotations

import contextlib
import os
import select
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from flycanon.core.services.query.rlm.sandbox import _proto
from flycanon.core.services.query.rlm.sandbox.runner import FD_ENV, TEXT_ENV

# The RPC functions the child may ask the parent to perform, mapped to the
# arity the parent enforces before dispatching to an injected handler. Anything
# outside this allowlist is rejected without touching a handler.
_RPC_ARITY = {
    "docs_keys": 0,
    "docs_getitem": 1,
    "docs_pages": 1,
    "docs_npages": 1,
    "docs_contains": 1,
    "llm": 1,
    "rlm": 2,
}

# Only these environment variables are passed to the child. Everything else --
# ANTHROPIC_API_KEY, AWS_*/AZURE_*, DATABASE_URL, REDIS*, FLYCANON_* secrets --
# is withheld so prompt-injected code can never read a credential.
_ENV_WHITELIST = ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "PYTHONHASHSEED")

_GRACE_SECONDS = 2.0  # how long to wait for a child to exit before SIGKILL.


@dataclass(frozen=True)
class BlockResult:
    """The outcome of one :meth:`SandboxExecutor.run_block` call.

    Exactly one of the three shapes is populated, distinguished by ``kind``:

    * ``"stdout"`` -- the block ran to completion; ``stdout`` holds its
      captured (capped) output.
    * ``"final"`` -- the block called ``final(...)``; ``final`` holds
      ``{answer, filings, pages, found}``.
    * ``"error"`` -- the block raised, timed out, or the child misbehaved;
      ``error`` holds a human-readable message / traceback.
    """

    kind: str
    stdout: str | None = None
    final: dict | None = None
    error: str | None = None


def _stdout(text: str) -> BlockResult:
    return BlockResult(kind="stdout", stdout=text)


def _final(payload: dict) -> BlockResult:
    return BlockResult(kind="final", final=payload)


def _error(message: str) -> BlockResult:
    return BlockResult(kind="error", error=message)


class SandboxExecutor:
    """Spawns and drives a sandboxed child REPL, servicing its capability RPCs.

    The capability handlers are injected by the caller (they close over the real
    document store / model client). The executor itself owns no secrets -- it
    only relays validated calls between the untrusted child and the handlers.
    """

    def __init__(
        self,
        *,
        docs_keys: Callable[[], list],
        docs_getitem: Callable[[str], str],
        docs_pages: Callable[[str], list],
        docs_npages: Callable[[str], int],
        docs_contains: Callable[[str], bool],
        llm: Callable[[str], str],
        rlm: Callable[[str, str], str],
        timeout: float = 30.0,
        text: str | None = None,
    ) -> None:
        self._handlers: dict[str, Callable] = {
            "docs_keys": docs_keys,
            "docs_getitem": docs_getitem,
            "docs_pages": docs_pages,
            "docs_npages": docs_npages,
            "docs_contains": docs_contains,
            "llm": llm,
            "rlm": rlm,
        }
        self._timeout = timeout
        self._text = text
        self._proc: subprocess.Popen | None = None
        self._parent_sock: socket.socket | None = None

    # -- lifecycle --
    def start(self) -> None:
        """Spawn the child with a scrubbed env and a private IPC socketpair."""
        if self._proc is not None:
            raise RuntimeError("sandbox already started")
        parent_sock, child_sock = socket.socketpair()
        child_fd = child_sock.fileno()
        env = self._child_env(child_fd)
        # ``pass_fds`` keeps the child socket fd open across the fork/exec; every
        # other parent fd is closed by ``close_fds`` (subprocess default True).
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "flycanon.core.services.query.rlm.sandbox.runner"],
            env=env,
            pass_fds=(child_fd,),
            close_fds=True,
        )
        child_sock.close()  # the child owns its end now; the parent drops it.
        self._parent_sock = parent_sock

    def _child_env(self, child_fd: int) -> dict:
        """A minimal, secret-free environment for the child."""
        env = {name: os.environ[name] for name in _ENV_WHITELIST if name in os.environ}
        # The child must import the flycanon package; if the parent runs from a
        # source tree without an installed dist, propagate sys.path explicitly.
        env.setdefault("PYTHONPATH", os.pathsep.join(p for p in sys.path if p))
        env[FD_ENV] = str(child_fd)
        if self._text is not None:
            env[TEXT_ENV] = self._text
        return env

    # -- the one operation --
    def run_block(self, code: str) -> BlockResult:
        """Run ``code`` in the child and return its outcome.

        Sends the ``exec`` command, then services child frames until a terminal
        one (``stdout`` / ``final`` / ``error``) arrives. ``rpc`` frames are
        validated and dispatched to the injected handlers. A per-block
        wall-clock timeout, a malformed frame, or an unknown op/fn kills the
        child and yields an ``error`` result.
        """
        if self._proc is None or self._parent_sock is None:
            raise RuntimeError("sandbox not started")
        try:
            self._send({"op": "exec", "code": code})
        except OSError as exc:
            self._kill()
            return _error(f"sandbox write failed: {exc}")
        return self._service_loop()

    def _service_loop(self) -> BlockResult:
        assert self._parent_sock is not None
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                return _error(f"sandbox timed out after {self._timeout:.1f}s")
            ready, _, _ = select.select([self._parent_sock], [], [], remaining)
            if not ready:
                self._kill()
                return _error(f"sandbox timed out after {self._timeout:.1f}s")
            try:
                frame = self._recv(deadline)
            except TimeoutError:
                # A multi-chunk frame stalled mid-read: the child announced N
                # bytes then dribbled or stopped. The per-recv deadline tripped,
                # so enforce the wall-clock timeout instead of blocking forever.
                self._kill()
                return _error(f"sandbox timed out after {self._timeout:.1f}s")
            except EOFError:
                self._kill()
                return _error("sandbox child exited unexpectedly")
            except _proto.ProtocolError as exc:
                self._kill()
                return _error(f"malformed child frame: {exc}")

            op = frame.get("op")
            if op == "stdout":
                return _stdout(str(frame.get("text", "")))
            if op == "final":
                return _final(
                    {
                        "answer": str(frame.get("answer", "")),
                        "filings": frame.get("filings"),
                        "pages": frame.get("pages"),
                        "found": bool(frame.get("found", True)),
                    }
                )
            if op == "error":
                return _error(str(frame.get("traceback", "")))
            if op == "rpc":
                result = self._dispatch_rpc(frame)
                if result is not None:  # a fatal validation failure already killed the child.
                    return result
                continue
            # Unknown op from the child: do not trust it, do not crash.
            self._kill()
            return _error(f"unknown child op: {op!r}")

    def _dispatch_rpc(self, frame: dict) -> BlockResult | None:
        """Validate and service one ``rpc`` frame; ``None`` means continue.

        Returns an ``error`` :class:`BlockResult` (after killing the child) only
        on a fatal protocol violation -- an unknown fn, a bad arg shape, or a
        write failure. A handler that itself raises is reported back *into* the
        child as a normal exception value so the model code can react, keeping
        the block alive.
        """
        fn = frame.get("fn")
        args = frame.get("args")
        if not isinstance(fn, str) or fn not in _RPC_ARITY:
            self._kill()
            return _error(f"unknown rpc fn: {fn!r}")
        arity = _RPC_ARITY[fn]
        if not isinstance(args, list) or len(args) != arity:
            self._kill()
            return _error(f"bad args for rpc {fn}: {args!r}")
        if not all(isinstance(a, str) for a in args):
            self._kill()
            return _error(f"non-string args for rpc {fn}: {args!r}")
        # The handler closes over real infra (doc store / model client) and may
        # raise (missing key, transient API error). Per this method's contract
        # such a failure is reported back *into* the child as an exception value
        # -- it must NOT propagate here and orphan the live child.
        try:
            reply = {"op": "result", "value": self._handlers[fn](*args)}
        except Exception as exc:  # noqa: BLE001 - relay any handler failure to the child
            reply = {"op": "result", "error": f"{type(exc).__name__}: {exc}"}
        try:
            self._send(reply)
        except (OSError, _proto.ProtocolError, TypeError) as exc:
            # OSError: socket write failed. ProtocolError: frame too large.
            # TypeError: a handler returned a non-JSON-serialisable value, so
            # json.dumps in encode() raised -- still our problem, not a crash.
            self._kill()
            return _error(f"sandbox result write failed: {exc}")
        return None

    def close(self) -> None:
        """Ask the child to shut down, then terminate and reap it."""
        if self._proc is None:
            return
        if self._parent_sock is not None:
            with contextlib.suppress(OSError):
                self._send({"op": "shutdown"})
        try:
            self._proc.wait(timeout=_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._kill()
        else:
            self._reap_socket()
            self._proc = None

    # -- framing helpers (json only; never pickle/eval) --
    def _send(self, frame: dict) -> None:
        assert self._parent_sock is not None
        self._parent_sock.sendall(_proto.encode(frame))

    def _recv(self, deadline: float) -> dict:
        """Decode one frame, bounding every underlying ``recv`` by ``deadline``.

        The child's frames are UNTRUSTED: a hostile/buggy child can announce a
        multi-chunk frame then stall, so each ``recv`` issued by ``_proto.decode``
        gets a fresh per-call timeout derived from the remaining wall-clock
        budget. When it runs out, ``recv`` raises ``TimeoutError`` and the
        service loop converts that into the documented per-block timeout.
        """
        assert self._parent_sock is not None
        sock = self._parent_sock

        def read(size: int) -> bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("sandbox recv deadline exceeded")
            sock.settimeout(remaining)
            return sock.recv(size)

        return _proto.decode(read)

    # -- teardown --
    def _kill(self) -> None:
        """SIGKILL the child and reap it; release the parent socket."""
        if self._proc is not None and self._proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.send_signal(signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=_GRACE_SECONDS)
        self._reap_socket()
        self._proc = None

    def _reap_socket(self) -> None:
        if self._parent_sock is not None:
            with contextlib.suppress(OSError):
                self._parent_sock.close()
            self._parent_sock = None
