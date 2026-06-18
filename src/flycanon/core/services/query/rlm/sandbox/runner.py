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

"""The sandbox child: a persistent restricted REPL driven over a pipe.

Run as ``python -m flycanon.core.services.query.rlm.sandbox.runner`` with the
IPC socket's file-descriptor number in ``$FLYCANON_SANDBOX_FD`` (or as argv[1]).
The child reads length-prefixed JSON command frames from the parent and replies
on the same fd. It reproduces the in-process engine's ``_exec`` behaviour --
persistent namespace, captured stdout, ``final`` sentinel, traceback on error --
but with **no secrets, no network, and no infrastructure objects**:

* The exec namespace exposes only the safe-builtins whitelist, ``re``, and
  capability *stubs*. Every stub (``docs``/``llm``/``rlm``/``final``) marshals
  its call to the parent as an RPC frame and blocks for the parent's result;
  the actual document store and model client live in the parent.
* At startup the child clamps itself with ``resource.setrlimit``: a CPU-time
  cap, an address-space cap, and ``RLIMIT_FSIZE = 0`` so it cannot create or
  grow any file (a defence-in-depth complement to the absent ``open`` builtin).

Commands (parent -> child): ``{op:'exec','code':str}`` and ``{op:'shutdown'}``.
Replies (child -> parent): ``{op:'stdout','text':str}`` after a normal exec,
``{op:'final',...}`` when the code calls ``final(...)``, ``{op:'error',
'traceback':str}`` on any other exception, and ``{op:'rpc','fn':...,'args':...}``
mid-exec (the parent answers with ``{op:'result','value':...}``).
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import resource
import sys
import traceback

from flycanon.core.services.query.rlm.safe_builtins import _SAFE_BUILTINS
from flycanon.core.services.query.rlm.sandbox import _proto

# The env var / fallback argv slot carrying the inherited IPC fd number.
FD_ENV = "FLYCANON_SANDBOX_FD"
# The text-mode (nested ``rlm``) startup text, if any.
TEXT_ENV = "FLYCANON_SANDBOX_TEXT"

_MAX_STDOUT = 4000  # mirrors the in-process engine's per-turn stdout cap.

# Resource limits clamped at startup (defence in depth around the restricted
# builtins). Generous enough for real document scanning; tight enough that a
# loop bomb or a fork bomb cannot run away or write to disk.
_CPU_SECONDS = 30  # RLIMIT_CPU -- wall-clock is separately enforced by the parent.
_ADDRESS_SPACE = 1 * 1024 * 1024 * 1024  # RLIMIT_AS -- 1 GiB.


class _Final(Exception):
    """Raised by the ``final`` stub to unwind the current ``exec``.

    The final frame is sent to the parent *before* this is raised, so the
    command loop only needs to swallow it and await the next command.
    """


class _Channel:
    """Blocking length-prefixed JSON frames over the inherited IPC fd."""

    def __init__(self, fd: int) -> None:
        # Buffered binary file over the raw fd; ``closefd=False`` so the fd's
        # lifetime stays owned by the process, not this wrapper.
        self._rx = os.fdopen(fd, "rb", buffering=0, closefd=False)
        self._tx = os.fdopen(fd, "wb", buffering=0, closefd=False)

    def send(self, frame: dict) -> None:
        self._tx.write(_proto.encode(frame))
        self._tx.flush()

    def recv(self) -> dict:
        return _proto.decode(self._rx.read)


class _DocsProxy:
    """The ``docs`` stub: every access becomes a docs_* RPC to the parent.

    Mirrors the :class:`DocCorpus` surface the model code expects
    (``keys`` / ``__getitem__`` / ``pages`` / ``npages`` / ``__contains__``);
    the real :class:`CanonDocStore` lives in the parent.
    """

    def __init__(self, channel: _Channel) -> None:
        self._channel = channel

    def _rpc(self, fn: str, *args):
        self._channel.send({"op": "rpc", "fn": fn, "args": list(args)})
        reply = self._channel.recv()
        # The parent only ever answers an rpc with a result frame; anything
        # else is a protocol violation we surface as an error inside exec.
        if reply.get("op") != "result":
            raise RuntimeError(f"unexpected reply to rpc {fn}: {reply.get('op')!r}")
        return reply.get("value")

    def keys(self):
        return self._rpc("docs_keys")

    def __getitem__(self, key: str):
        return self._rpc("docs_getitem", key)

    def pages(self, key: str):
        return self._rpc("docs_pages", key)

    def npages(self, key: str):
        return self._rpc("docs_npages", key)

    def __contains__(self, key: object):
        return self._rpc("docs_contains", key)


def _make_namespace(channel: _Channel, text: str | None) -> dict:
    """Build the persistent exec namespace exposed to model code.

    Restricted builtins, ``re``, the capability stubs, and either ``docs``
    (root mode) or ``text`` (nested ``rlm`` mode).
    """

    def llm(prompt):
        channel.send({"op": "rpc", "fn": "llm", "args": [str(prompt)]})
        reply = channel.recv()
        if reply.get("op") != "result":
            raise RuntimeError(f"unexpected reply to rpc llm: {reply.get('op')!r}")
        return reply.get("value")

    def rlm(question, txt):
        channel.send({"op": "rpc", "fn": "rlm", "args": [str(question), str(txt)]})
        reply = channel.recv()
        if reply.get("op") != "result":
            raise RuntimeError(f"unexpected reply to rpc rlm: {reply.get('op')!r}")
        return reply.get("value")

    def final(answer, filings=None, pages=None, found=True):
        channel.send(
            {
                "op": "final",
                "answer": str(answer),
                "filings": filings,
                "pages": pages,
                "found": bool(found),
            }
        )
        raise _Final

    ns: dict = {
        "__builtins__": _SAFE_BUILTINS,
        "re": re,
        "llm": llm,
        "rlm": rlm,
        "final": final,
    }
    if text is None:
        ns["docs"] = _DocsProxy(channel)
    else:
        ns["text"] = text
    return ns


def _apply_rlimits() -> None:
    """Clamp CPU, address space, and (to zero) file size for this process."""
    resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (_ADDRESS_SPACE, _ADDRESS_SPACE))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def _run_exec(code: str, ns: dict, channel: _Channel) -> None:
    """Execute one block; emit the resulting stdout/final/error frame.

    Reproduces the in-process engine's contract: stdout captured and capped,
    ``_Final`` already reported (just swallowed here), any other exception
    reported as a traceback. The namespace ``ns`` persists across calls.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code, ns)  # noqa: S102 - restricted builtins; subprocess sandbox
    except _Final:
        # The final frame was already sent by the stub before it raised.
        return
    except BaseException:  # noqa: BLE001 - report any failure back to the parent
        channel.send({"op": "error", "traceback": traceback.format_exc()})
        return
    channel.send({"op": "stdout", "text": buf.getvalue()[:_MAX_STDOUT]})


def _resolve_fd() -> int:
    value = os.environ.get(FD_ENV)
    if value is None and len(sys.argv) > 1:
        value = sys.argv[1]
    if value is None:
        raise SystemExit(f"sandbox runner: no IPC fd (set ${FD_ENV} or pass argv[1])")
    return int(value)


def main() -> None:
    _apply_rlimits()
    channel = _Channel(_resolve_fd())
    text = os.environ.get(TEXT_ENV)
    ns = _make_namespace(channel, text)
    while True:
        try:
            command = channel.recv()
        except EOFError:
            return  # parent closed the pipe -- exit cleanly.
        op = command.get("op")
        if op == "shutdown":
            return
        if op == "exec":
            _run_exec(str(command.get("code", "")), ns, channel)
            continue
        # Unknown command: report it but keep serving (the parent drives us).
        channel.send({"op": "error", "traceback": f"unknown command op: {op!r}"})


if __name__ == "__main__":
    main()
