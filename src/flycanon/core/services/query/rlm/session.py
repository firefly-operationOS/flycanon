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

"""The Recursive Language Model engine: a CodeAct REPL over context-as-a-variable.

Faithful to alexzhang13/rlm's mechanism (adapted to Anthropic + flycanon):

* The orchestrator LM runs a loop (up to ``max_iters``). Each turn it emits a
  ``python`` tool call; we ``exec`` it in a persistent, **restricted** namespace
  and feed the captured stdout back as the next user turn.
* The corpus is the variable ``docs`` -- the model inspects it with code instead
  of reading it in the prompt. From inside the code it can make **recursive
  sub-calls**: ``llm(prompt)`` (a flat sub-LM call on a chunk) and
  ``rlm(question, text)`` (a nested REPL over a slice, depth-limited -- at
  ``max_depth`` it degrades to ``llm``).
* The loop stops when a code block calls
  ``final(answer, filings=..., pages=..., found=...)``; ``found=False`` flags a
  structured no-answer that flows out of the loop as the third return value.

``exec`` runs model-written code, so the namespace exposes only a safe builtins
subset (text processing, no open/import/eval) plus ``re``, ``llm``, ``rlm``,
``final``, ``print``.

The engine is **corpus-agnostic**: ``docs`` is duck-typed via the
:class:`DocCorpus` protocol, so no concrete document store is imported here.
"""

from __future__ import annotations

import io
import re
import traceback
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any, Protocol, runtime_checkable

from flycanon.core.services.query.rlm.client import AnthropicClient


@runtime_checkable
class DocCorpus(Protocol):
    """Duck-typed document store the REPL inspects with code.

    The concrete implementation (``CanonDocStore``) arrives in a later PR; the
    engine only relies on this dict-like surface plus page accessors.
    """

    def keys(self) -> list[str]: ...

    def __getitem__(self, key: str) -> str: ...

    def __contains__(self, key: object) -> bool: ...

    def pages(self, key: str) -> list[str]: ...

    def npages(self, key: str) -> int: ...


# The single tool the orchestrator drives: a persistent Python REPL. Claude emits
# native tool-use blocks for this rather than markdown code fences, so execution
# is reliable.
_PY_TOOL = [
    {
        "name": "python",
        "description": "Execute Python in the persistent REPL and return its stdout. The "
        "namespace has `docs`, `re`, `llm`, `rlm`, `final`. Call "
        "`final(answer, filings=[...], pages=[...], found=True)` from code to finish; "
        "pass `found=False` when the documents do not contain the answer.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute"}},
            "required": ["code"],
        },
    }
]

# ---------------------------------------------------------------------------
# restricted exec sandbox
# ---------------------------------------------------------------------------
_SAFE_BUILTIN_NAMES = (
    "print", "len", "range", "str", "list", "dict", "set", "tuple",
    "sorted", "enumerate", "min", "max", "sum", "any", "all", "zip",
    "map", "filter", "int", "float", "bool", "abs", "round", "repr",
    "slice", "isinstance", "reversed", "True", "False", "None",
)  # fmt: skip


def _build_safe_builtins() -> dict:
    """A whitelist of harmless builtins -- no ``open``, ``import``, ``eval``."""
    src = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    return {name: src[name] for name in _SAFE_BUILTIN_NAMES if name in src}


_SAFE_BUILTINS = _build_safe_builtins()
_MAX_STDOUT = 4000  # truncate each turn's printed output fed back to the model
# Cap on cited page text. A dense 10-K page is ~3-5k chars; this keeps the
# whole page the model read (structure intact) rather than a tiny prefix.
_CITATION_CONTENT_CHARS = 4000


class _Final(Exception):
    def __init__(self, answer: str, citations: list[dict], no_answer: bool = False):
        self.answer, self.citations, self.no_answer = answer, citations, no_answer


_WORD = re.compile(r"[a-zA-Z][a-zA-Z']+")


def _best_page(question: str, pages: list[str]) -> int:
    """Deterministic keyword-best page -- used to give the judge a real evidence
    page when the model cites a filing without a specific page."""
    q = {w.lower() for w in _WORD.findall(question) if len(w) > 2}
    if not pages:
        return 0
    return max(
        range(len(pages)),
        key=lambda i: len(q & {w.lower() for w in _WORD.findall(pages[i])}),
    )


SYSTEM = """\
You are a Recursive Language Model solving a question about a document corpus by
writing Python in a REPL. You do NOT see the documents in this prompt -- they are
the variable `docs`, which you inspect with code.

Environment (persists across turns):
- `docs`: dict-like over the corpus. `docs.keys()` -> document ids. `docs[fid]` ->
  full document text. `docs.pages(fid)` -> list of page strings.
  `docs.npages(fid)` -> page count.
- `llm(prompt: str) -> str`: a sub-LM call. Use it to extract/compute from a chunk
  you pass in (e.g. a statement page) when reading is easier delegated.
- `rlm(question: str, text: str) -> str`: a recursive sub-call that itself reasons
  over a large `text`. Use for big sections you want decomposed.
- `re` is available. `final(answer, filings=[...], pages=[...])`: call this to
  finish, citing the document id(s) and page number(s) you used.

Rules:
- Use the `python` tool to run code; its stdout is returned to you. Call it
  repeatedly, building on the persistent namespace.
- Strategy: find the right document from `docs.keys()` (match the entity in the
  question), read the relevant pages (search the text for the figure), extract the
  answer, then call `final(...)`. Print intermediate findings so you can see them.
  Keep printed output small (slice/grep; don't print whole documents).
- Always finish by calling `final(...)`. If the evidence is not present, call
  `final('the documents do not contain this', filings=[...], found=False)`."""

_NESTED_SYSTEM = """\
You are a recursive sub-model answering a question over a text passage held in the
variable `text` (a string). Inspect it with the `python` tool. Helpers: `llm(prompt)`,
`re`, `print`, and `final(answer)` to finish. Its stdout returns to you. Search `text`
for the figure, extract it, then call `final(...)`. If the evidence is not present, call
`final('the documents do not contain this', found=False)`."""


class RLMSession:
    """One root (or nested) CodeAct REPL run against a corpus or text slice."""

    def __init__(
        self,
        client: AnthropicClient,
        depth: int = 0,
        max_depth: int = 1,
        max_iters: int = 8,
        sub_budget: int = 12,
        on_turn: Callable[[int, list[str]], None] | None = None,
    ):
        self.client = client
        self.depth, self.max_depth = depth, max_depth
        self.max_iters, self.sub_budget = max_iters, sub_budget
        self.sub_calls = 0
        self.turns = 0
        # Optional per-turn progress hook. Fired from the (synchronous) REPL
        # worker thread after each orchestrator turn so a streaming caller can
        # surface live progress; ``None`` on the non-streaming path. A callback
        # error must never break the REPL, so the call site guards it.
        self.on_turn = on_turn

    # -- recursive helpers exposed into the sandbox --
    def _llm(self, prompt: str) -> str:
        if self.sub_calls >= self.sub_budget:
            return "[sub-call budget exhausted]"
        self.sub_calls += 1
        return self.client.complete(str(prompt)[:60000])

    def _rlm(self, question: str, text: str) -> str:
        if self.depth + 1 >= self.max_depth or self.sub_calls >= self.sub_budget:
            return self._llm(f"{question}\n\nTEXT:\n{str(text)[:40000]}")
        self.sub_calls += 1
        sub = RLMSession(
            self.client,
            self.depth + 1,
            self.max_depth,
            max_iters=4,
            sub_budget=max(2, self.sub_budget - self.sub_calls),
        )
        ans, _cites, _no_answer = sub.run_over_text(question, str(text))
        return ans

    # -- the two entry points share one CodeAct loop --
    def run(self, question: str, docs: DocCorpus) -> tuple[str, list[dict], bool]:
        ns = {
            "docs": docs,
            "re": re,
            "llm": self._llm,
            "rlm": self._rlm,
            "__builtins__": _SAFE_BUILTINS,
        }
        return self._loop(SYSTEM, f"Question: {question}\n\nSolve it.", ns, docs, question)

    def run_over_text(self, question: str, text: str) -> tuple[str, list[dict], bool]:
        ns = {
            "text": text,
            "re": re,
            "llm": self._llm,
            "rlm": self._rlm,
            "__builtins__": _SAFE_BUILTINS,
        }
        return self._loop(
            _NESTED_SYSTEM, f"Question: {question}\n\nSolve it from `text`.", ns, None, question
        )

    def _exec(self, code: str, ns: dict):
        """Run one code block; return (stdout, final_or_None)."""
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                exec(code, ns)  # noqa: S102 - restricted builtins; engine sandbox
            return buf.getvalue() or "(no output)", None
        except _Final as fin:
            return None, fin
        except Exception:  # noqa: BLE001 - feed the traceback back to the model
            return buf.getvalue() + "\n" + traceback.format_exc(limit=3), None

    def _loop(self, system: str, first_user: str, ns: dict, docs, question):
        def final(answer, filings=None, pages=None, found=True):
            raise _Final(
                str(answer),
                self._citations(filings, pages, docs, question),
                no_answer=not found,
            )

        ns["final"] = final

        messages: list[dict[str, Any]] = [{"role": "user", "content": first_user}]
        for _ in range(self.max_iters):
            self.turns += 1
            if self.on_turn is not None:
                # ``docs`` is the corpus on the root path (carries ``.accessed``)
                # and ``None`` on the nested-text path. A callback failure must
                # never break the REPL.
                try:
                    self.on_turn(self.turns, list(docs.accessed) if docs is not None else [])
                except Exception:  # noqa: BLE001 -- progress hook must not break the loop
                    pass
            resp = self.client.chat_raw(messages, system, _PY_TOOL)
            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if not tool_uses:  # model answered in text without running code
                text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                # best-effort plain-text answer: not a structured no-answer.
                return text.strip(), self._citations(None, None, docs, question), False
            results = []
            for tu in tool_uses:
                stdout, fin = self._exec(str(tu.get("input", {}).get("code", "")), ns)
                if fin is not None:
                    return fin.answer, fin.citations, fin.no_answer
                # stdout is only None on the _Final path, which returned above.
                assert stdout is not None
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": stdout[:_MAX_STDOUT],
                    }
                )
            messages.append({"role": "user", "content": results})
        # ran out of turns -> ask for a direct answer from the transcript
        messages.append({"role": "user", "content": "Stop now and state your final answer as plain text."})
        text = "".join(
            b.get("text", "")
            for b in self.client.chat_raw(messages, system, _PY_TOOL).get("content", [])
            if b.get("type") == "text"
        )
        # ran out of iterations: forced best-effort answer, not a structured no-answer.
        return text.strip(), self._citations(None, None, docs, question), False

    def _citations(self, filings, pages, docs, question) -> list[dict]:
        if not filings:
            return []
        if isinstance(filings, str):
            filings = [filings]
        pages = pages if isinstance(pages, (list, tuple)) else [pages] if pages is not None else []
        cites = []
        for i, fid in enumerate(filings):
            page = pages[i] if i < len(pages) and pages[i] is not None else None
            if docs is not None and isinstance(fid, str) and fid in docs:
                pg_list = docs.pages(fid)
                if page is None:
                    page = _best_page(question, pg_list)
                page = max(0, min(int(page), len(pg_list) - 1))
                # Keep the page's text structure (newlines / table layout) -- the
                # judge needs the actual page the model read, not a flattened
                # prefix. Financial statements align labels to numbers by row.
                content = pg_list[page].strip()[:_CITATION_CONTENT_CHARS]
            else:
                content = ""
            cites.append({"filing": fid, "page": page, "content": content})
        return cites
