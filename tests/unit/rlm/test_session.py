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

"""Unit tests for the RLMSession CodeAct REPL.

The LLM is fully faked (a scripted ``chat_raw``/``complete``); the corpus is an
in-memory dict-backed object implementing the :class:`DocCorpus` protocol. No
network, no API key.
"""

from __future__ import annotations

from flycanon.core.services.query.rlm.safe_builtins import _SAFE_BUILTINS
from flycanon.core.services.query.rlm.sandbox.executor import BlockResult, SandboxExecutor
from flycanon.core.services.query.rlm.session import (
    _CITATION_CONTENT_CHARS,
    DocCorpus,
    RLMSession,
    _best_page,
)


class FakeDocs:
    """Dict-backed corpus implementing the DocCorpus protocol for tests."""

    def __init__(self, data: dict[str, list[str]]):
        self._pages = data  # fid -> list of page strings

    def keys(self):
        return self._pages.keys()

    def __getitem__(self, key: str) -> str:
        return "\n".join(self._pages[key])

    def __contains__(self, key: object) -> bool:
        return key in self._pages

    def pages(self, key: str) -> list[str]:
        return self._pages[key]

    def npages(self, key: str) -> int:
        return len(self._pages[key])


class FakeClient:
    """Replays a scripted list of chat_raw responses; records complete() calls."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.chat_calls: list[dict] = []
        self.complete_calls: list[str] = []

    def chat_raw(self, messages, system, tools, model=None, max_tokens=1500):
        # snapshot the message list -- the loop mutates it in place across turns
        self.chat_calls.append({"messages": list(messages), "system": system})
        return self._responses.pop(0)

    def complete(self, prompt, system="", model=None, max_tokens=1000):
        self.complete_calls.append(prompt)
        return f"sub-answer:{prompt[:20]}"


def _tool_use(code: str, tool_id: str = "t1") -> dict:
    return {"content": [{"type": "tool_use", "id": tool_id, "input": {"code": code}}]}


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def test_fakedocs_satisfies_protocol():
    docs = FakeDocs({"A": ["p0", "p1"]})
    assert isinstance(docs, DocCorpus)


def test_final_terminates_loop_with_citations():
    docs = FakeDocs({"ACME_2020": ["revenue was 100", "other page about costs"]})
    code = "final('100', filings=['ACME_2020'], pages=[0])"
    client = FakeClient([_tool_use(code)])
    answer, cites, _no_answer = RLMSession(client).run("what was revenue?", docs)
    assert answer == "100"
    assert len(cites) == 1
    assert cites[0]["filing"] == "ACME_2020"
    assert cites[0]["page"] == 0
    assert "revenue was 100" in cites[0]["content"]
    # only one orchestrator turn was needed
    assert len(client.chat_calls) == 1


def test_final_default_reports_found():
    docs = FakeDocs({"A": ["revenue was 100"]})
    client = FakeClient([_tool_use("final('100', filings=['A'], pages=[0])")])
    answer, cites, no_answer = RLMSession(client).run("revenue?", docs)
    assert answer == "100"
    assert len(cites) == 1
    # final() defaults to found=True -> no_answer is False
    assert no_answer is False


def test_final_found_false_reports_no_answer():
    docs = FakeDocs({"A": ["nothing relevant here"]})
    code = "final('the documents do not contain this', filings=['A'], found=False)"
    client = FakeClient([_tool_use(code)])
    answer, cites, no_answer = RLMSession(client).run("revenue?", docs)
    assert answer == "the documents do not contain this"
    # the citation is still carried, but the structured flag marks a no-answer
    assert cites[0]["filing"] == "A"
    assert no_answer is True


def test_text_only_answer_is_not_no_answer():
    docs = FakeDocs({"A": ["p"]})
    client = FakeClient([_text("plain best-effort answer")])
    _answer, _cites, no_answer = RLMSession(client).run("q", docs)
    assert no_answer is False


def test_out_of_iters_is_not_no_answer():
    docs = FakeDocs({"A": ["p"]})
    loops = [_tool_use("print('working')", f"t{i}") for i in range(2)]
    client = FakeClient(loops + [_text("forced final")])
    _answer, _cites, no_answer = RLMSession(client, max_iters=2).run("q", docs)
    assert no_answer is False


def test_multi_turn_then_final():
    docs = FakeDocs({"ACME_2020": ["intro", "the answer is 42"]})
    client = FakeClient(
        [
            _tool_use("print(docs.npages('ACME_2020'))"),
            _tool_use("final('42', filings=['ACME_2020'], pages=[1])"),
        ]
    )
    answer, cites, _no_answer = RLMSession(client).run("q", docs)
    assert answer == "42"
    assert cites[0]["page"] == 1
    assert len(client.chat_calls) == 2
    # the printed stdout from turn 1 was fed back as a tool_result
    second_user_msg = client.chat_calls[1]["messages"][-1]["content"][0]
    assert second_user_msg["type"] == "tool_result"
    assert "2" in second_user_msg["content"]


def test_sandbox_forbids_open_and_import():
    docs = FakeDocs({"A": ["x"]})
    # `open` is not in the safe builtins -> NameError -> traceback fed back, then final
    client = FakeClient(
        [
            _tool_use("open('/etc/passwd')"),
            _tool_use("final('done', filings=['A'])"),
        ]
    )
    answer, _cites, _no_answer = RLMSession(client).run("q", docs)
    assert answer == "done"
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "NameError" in tool_result["content"]


def test_safe_builtins_exclude_dangerous_names():
    for name in ("open", "__import__", "eval", "exec", "compile", "input", "globals"):
        assert name not in _SAFE_BUILTINS
    for name in ("len", "range", "str", "sum", "sorted", "print"):
        assert name in _SAFE_BUILTINS


def test_import_statement_blocked_in_exec():
    docs = FakeDocs({"A": ["x"]})
    client = FakeClient(
        [
            _tool_use("import os\nprint(os.getcwd())"),
            _tool_use("final('ok', filings=['A'])"),
        ]
    )
    answer, _cites, _no_answer = RLMSession(client).run("q", docs)
    assert answer == "ok"
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "ImportError" in tool_result["content"] or "NameError" in tool_result["content"]


def test_best_page_picks_keyword_match():
    pages = [
        "this page is about marketing and brand",
        "total revenue for the fiscal year was strong",
        "legal disclosures and footnotes",
    ]
    assert _best_page("what was the total revenue", pages) == 1


def test_best_page_empty_returns_zero():
    assert _best_page("anything", []) == 0


def test_citation_fills_best_page_when_unspecified():
    docs = FakeDocs({"A": ["intro page", "net income figure is here"]})
    client = FakeClient([_tool_use("final('x', filings=['A'])")])
    _answer, cites, _no_answer = RLMSession(client).run("net income figure", docs)
    # no page given -> _best_page chosen deterministically (page 1 has the keywords)
    assert cites[0]["page"] == 1


def test_citation_clamps_out_of_range_page():
    docs = FakeDocs({"A": ["only page"]})
    client = FakeClient([_tool_use("final('x', filings=['A'], pages=[99])")])
    _answer, cites, _no_answer = RLMSession(client).run("q", docs)
    assert cites[0]["page"] == 0


def test_unknown_filing_has_empty_content():
    docs = FakeDocs({"A": ["p"]})
    client = FakeClient([_tool_use("final('x', filings=['MISSING'])")])
    _answer, cites, _no_answer = RLMSession(client).run("q", docs)
    assert cites[0]["filing"] == "MISSING"
    assert cites[0]["content"] == ""


def test_citation_content_preserves_newlines():
    # a financial-statement-like page where layout (newlines) carries meaning
    page = "Consolidated Balance Sheet\nTotal assets        1,234\nTotal liabilities     567"
    docs = FakeDocs({"A": [page]})
    client = FakeClient([_tool_use("final('x', filings=['A'], pages=[0])")])
    _answer, cites, _no_answer = RLMSession(client).run("total assets", docs)
    # structure is preserved -- newlines are NOT collapsed to spaces
    assert "\n" in cites[0]["content"]
    assert cites[0]["content"] == page


def test_citation_content_capped_not_500():
    # a page longer than the old 500-char cap but within the new cap
    page = "\n".join(f"row {i} value {i * 7}" for i in range(80))
    assert 500 < len(page) <= _CITATION_CONTENT_CHARS
    docs = FakeDocs({"A": [page]})
    client = FakeClient([_tool_use("final('x', filings=['A'], pages=[0])")])
    _answer, cites, _no_answer = RLMSession(client).run("rows", docs)
    # the full page survives -- more than the old 500-char prefix
    assert len(cites[0]["content"]) > 500
    assert cites[0]["content"] == page


def test_citation_content_truncated_at_cap():
    # a page exceeding the cap is truncated to exactly _CITATION_CONTENT_CHARS
    page = "x" * (_CITATION_CONTENT_CHARS + 1000)
    docs = FakeDocs({"A": [page]})
    client = FakeClient([_tool_use("final('x', filings=['A'], pages=[0])")])
    _answer, cites, _no_answer = RLMSession(client).run("q", docs)
    assert len(cites[0]["content"]) == _CITATION_CONTENT_CHARS


def test_text_only_answer_without_tool_use():
    docs = FakeDocs({"A": ["p"]})
    client = FakeClient([_text("  the answer is plain  ")])
    answer, cites, _no_answer = RLMSession(client).run("q", docs)
    assert answer == "the answer is plain"
    assert cites == []


def test_out_of_iters_asks_for_plain_answer():
    docs = FakeDocs({"A": ["p"]})
    # every turn runs code but never calls final; loop exhausts then asks for text
    loops = [_tool_use("print('still working')", f"t{i}") for i in range(2)]
    client = FakeClient(loops + [_text("forced final")])
    answer, _cites, _no_answer = RLMSession(client, max_iters=2).run("q", docs)
    assert answer == "forced final"
    # 2 loop turns + 1 forced-answer call
    assert len(client.chat_calls) == 3
    assert client.chat_calls[-1]["messages"][-1]["content"] == (
        "Stop now and state your final answer as plain text."
    )


def test_llm_helper_callable_from_sandbox():
    docs = FakeDocs({"A": ["pageA"]})
    client = FakeClient(
        [
            _tool_use("print(llm('extract the number from this chunk'))"),
            _tool_use("final('ok', filings=['A'])"),
        ]
    )
    answer, _cites, _no_answer = RLMSession(client).run("q", docs)
    assert answer == "ok"
    assert client.complete_calls == ["extract the number from this chunk"]
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "sub-answer:" in tool_result["content"]


def test_sub_call_budget_exhaustion():
    docs = FakeDocs({"A": ["p"]})
    client = FakeClient(
        [
            _tool_use("print(llm('one')); print(llm('two'))"),
            _tool_use("final('ok', filings=['A'])"),
        ]
    )
    session = RLMSession(client, sub_budget=1)
    answer, _cites, _no_answer = session.run("q", docs)
    assert answer == "ok"
    # budget 1 -> first llm runs, second returns the exhausted sentinel
    assert client.complete_calls == ["one"]
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "[sub-call budget exhausted]" in tool_result["content"]


class _AccessedDocs(FakeDocs):
    """FakeDocs that also records ``accessed`` keys (like the real corpus)."""

    def __init__(self, data: dict[str, list[str]], accessed: list[str] | None = None):
        super().__init__(data)
        self.accessed = accessed if accessed is not None else []


def test_on_turn_fires_once_per_turn_with_increasing_numbers():
    docs = _AccessedDocs({"A": ["intro", "the answer is 42"]}, accessed=["A"])
    client = FakeClient(
        [
            _tool_use("print('looking')"),
            _tool_use("print('still looking')"),
            _tool_use("final('42', filings=['A'], pages=[1])"),
        ]
    )
    seen: list[tuple[int, list[str]]] = []
    RLMSession(client, on_turn=lambda turn, accessed: seen.append((turn, list(accessed)))).run("q", docs)
    # one callback per orchestrator turn (3 turns -> 3 calls), increasing numbers
    assert [turn for turn, _ in seen] == [1, 2, 3]
    # the accessed keys snapshot is forwarded
    assert all(accessed == ["A"] for _, accessed in seen)


def test_on_turn_none_is_a_no_op():
    docs = _AccessedDocs({"A": ["x"]}, accessed=["A"])
    client = FakeClient([_tool_use("final('ok', filings=['A'])")])
    # default on_turn=None must not raise and must not affect the answer
    answer, _cites, _no_answer = RLMSession(client).run("q", docs)
    assert answer == "ok"


def test_on_turn_exception_never_breaks_the_repl():
    docs = _AccessedDocs({"A": ["the answer is here"]}, accessed=["A"])
    client = FakeClient([_tool_use("final('done', filings=['A'])")])

    def boom(turn, accessed):
        raise RuntimeError("callback blew up")

    # a raising callback is swallowed -- the REPL still produces its answer
    answer, _cites, _no_answer = RLMSession(client, on_turn=boom).run("q", docs)
    assert answer == "done"


def test_rlm_degrades_to_llm_at_max_depth():
    docs = FakeDocs({"A": ["p"]})
    client = FakeClient(
        [
            _tool_use("print(rlm('inner question', 'some big text'))"),
            _tool_use("final('ok', filings=['A'])"),
        ]
    )
    # max_depth=1 means a depth-0 session's rlm() degrades straight to llm()
    session = RLMSession(client, max_depth=1)
    answer, _cites, _no_answer = session.run("q", docs)
    assert answer == "ok"
    assert len(client.complete_calls) == 1
    assert "inner question" in client.complete_calls[0]


# ---------------------------------------------------------------------------
# subprocess mode -- REAL end-to-end through the sandbox child (no network).
# The FakeClient scripts the orchestrator turns; the child execs the real code,
# reaching this in-memory FakeDocs / FakeClient only through the executor's RPCs.
# ---------------------------------------------------------------------------


def test_subprocess_mode_reads_docs_and_finalises():
    docs = FakeDocs({"ACME_2020": ["intro page", "revenue was 100 this year"]})
    # turn 1: prove the child reaches the real corpus via RPC (keys/pages/getitem).
    probe = (
        "print(list(docs.keys()))\n"
        "print(docs.npages('ACME_2020'))\n"
        "print('ACME_2020' in docs)\n"
        "print(docs.pages('ACME_2020')[1])\n"
        "print('100' in docs['ACME_2020'])"
    )
    client = FakeClient(
        [
            _tool_use(probe),
            _tool_use("final('100', filings=['ACME_2020'], pages=[1], found=True)"),
        ]
    )
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, cites, no_answer = session.run("what was revenue?", docs)
    assert answer == "100"
    assert no_answer is False
    # citations are built PARENT-SIDE from the child's raw final frame.
    assert len(cites) == 1
    assert cites[0]["filing"] == "ACME_2020"
    assert cites[0]["page"] == 1
    assert "revenue was 100" in cites[0]["content"]
    # the child's stdout (its RPC reads) was fed back as turn 2's tool_result.
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "ACME_2020" in tool_result["content"]
    assert "True" in tool_result["content"]


def test_subprocess_mode_found_false_is_no_answer():
    docs = FakeDocs({"A": ["nothing relevant"]})
    code = "final('not present', filings=['A'], found=False)"
    client = FakeClient([_tool_use(code)])
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, cites, no_answer = session.run("revenue?", docs)
    assert answer == "not present"
    assert no_answer is True
    assert cites[0]["filing"] == "A"


def test_subprocess_mode_error_traceback_fed_back():
    docs = FakeDocs({"A": ["x"]})
    # `open` is not in the child's safe builtins -> NameError -> the traceback is
    # fed back to the model as the turn's tool_result, then the next turn finals.
    client = FakeClient(
        [
            _tool_use("open('/etc/passwd')"),
            _tool_use("final('done', filings=['A'])"),
        ]
    )
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, _cites, _no_answer = session.run("q", docs)
    assert answer == "done"
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "NameError" in tool_result["content"]


def test_subprocess_mode_llm_rpc_reaches_parent_client():
    docs = FakeDocs({"A": ["pageA"]})
    client = FakeClient(
        [
            _tool_use("print(llm('extract the number'))"),
            _tool_use("final('ok', filings=['A'])"),
        ]
    )
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, _cites, _no_answer = session.run("q", docs)
    assert answer == "ok"
    # the child's llm() RPC was serviced by the parent's real FakeClient.
    assert client.complete_calls == ["extract the number"]
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "sub-answer:" in tool_result["content"]


def test_subprocess_mode_run_over_text_uses_text_mode():
    # text-mode: no corpus, the child exposes `text` (not `docs`).
    client = FakeClient(
        [
            _tool_use("print('net income' in text)\nprint(len(text))"),
            _tool_use("final('found it')"),
        ]
    )
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, cites, _no_answer = session.run_over_text("q", "the net income line is here")
    assert answer == "found it"
    # text-mode has no corpus -> no parent-side citation content.
    assert cites == []
    tool_result = client.chat_calls[1]["messages"][-1]["content"][0]
    assert "True" in tool_result["content"]


class _DeadExecutor(SandboxExecutor):
    """A sandbox stand-in whose child is already dead: every block terminates.

    Used to prove the session degrades gracefully (no exception, no 'sandbox not
    started') when the real child dies mid-query -- the executor returns a
    ``terminated`` BlockResult and the loop falls through to the parent-side
    plain-text fallback.
    """

    def __init__(self):
        # inert handlers just to satisfy the base constructor; never called.
        noop = lambda *_a: ""  # noqa: E731
        super().__init__(
            docs_keys=lambda: [],
            docs_getitem=noop,
            docs_pages=noop,
            docs_npages=noop,
            docs_contains=noop,
            llm=noop,
            rlm=noop,
        )
        self.closed = False

    def start(self) -> None:  # no real child to spawn
        pass

    def run_block(self, code: str) -> BlockResult:
        return BlockResult(kind="terminated", error="sandbox child exited unexpectedly")

    def close(self) -> None:
        self.closed = True


def _assert_tool_uses_answered(messages):
    """Assert the transcript honours the Messages API tool_use/tool_result
    contract: every tool_use block in an assistant turn is answered by a
    tool_result with the matching id in the *immediately following* user
    message. A dangling tool_use is exactly what triggers the real API's
    HTTP 400 'Each tool_use block must have a corresponding tool_result block
    in the next message'.
    """
    for i, msg in enumerate(messages):
        if msg["role"] != "assistant":
            continue
        content = msg["content"]
        if not isinstance(content, list):
            continue
        tool_use_ids = [b["id"] for b in content if b.get("type") == "tool_use"]
        if not tool_use_ids:
            continue
        assert i + 1 < len(messages), "tool_use turn has no following message"
        nxt = messages[i + 1]
        assert nxt["role"] == "user", "tool_use must be answered in the next user message"
        nxt_content = nxt["content"]
        # A plain-text user turn (str content) carries no tool_result at all, so
        # every tool_use is dangling -- this is precisely the malformed shape the
        # real API 400s on.
        assert isinstance(nxt_content, list), (
            f"tool_use turn followed by a non-tool_result message: {nxt_content!r}"
        )
        result_ids = {b["tool_use_id"] for b in nxt_content if b.get("type") == "tool_result"}
        missing = [tid for tid in tool_use_ids if tid not in result_ids]
        assert not missing, f"dangling tool_use(s) with no tool_result: {missing}"


def test_subprocess_mode_terminated_degrades_to_plaintext_answer(monkeypatch):
    # The child dies mid-query (executor returns a terminated result). The session
    # must NOT raise and must NOT call run_block again on the dead executor: it
    # falls through to the out-of-iters fallback, which asks the PARENT's client
    # for a plain-text answer (works with a dead sandbox).
    docs = FakeDocs({"A": ["some page"]})
    dead = _DeadExecutor()
    monkeypatch.setattr(RLMSession, "_build_executor", lambda self, d, t: dead)

    client = FakeClient(
        [
            _tool_use("print('this block dies')"),  # turn 1 -> terminated -> break
            _text("best-effort answer from the transcript"),  # the fallback turn
        ]
    )
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, cites, no_answer = session.run("q", docs)

    assert answer == "best-effort answer from the transcript"
    assert no_answer is False  # forced best-effort answer, not a structured no-answer
    assert cites == []
    # the fallback prompt was sent and only ONE block was attempted (no retry on
    # the dead executor).
    assert "state your final answer" in client.chat_calls[-1]["messages"][-1]["content"]
    # the executor is still closed via the session's try/finally.
    assert dead.closed is True
    # the transcript handed to the fallback chat_raw must be API-valid: the
    # assistant tool_use that ran on the dead sandbox is answered by a
    # tool_result -- no dangling tool_use that the real API would 400 on.
    _assert_tool_uses_answered(client.chat_calls[-1]["messages"])


def _multi_tool_use(codes: list[str]) -> dict:
    return {
        "content": [
            {"type": "tool_use", "id": f"t{i}", "input": {"code": code}} for i, code in enumerate(codes)
        ]
    }


def test_subprocess_mode_terminated_multi_tool_all_blocks_answered(monkeypatch):
    # The assistant emits TWO tool_use blocks in one turn; the first kills the
    # sandbox. Both tool_use blocks (the dead one and the never-run sibling)
    # must still be answered by a tool_result so the fallback transcript stays
    # API-valid -- no dangling tool_use of either kind.
    docs = FakeDocs({"A": ["some page"]})
    dead = _DeadExecutor()
    monkeypatch.setattr(RLMSession, "_build_executor", lambda self, d, t: dead)

    client = FakeClient(
        [
            _multi_tool_use(["print('first dies')", "print('never runs')"]),
            _text("best-effort answer"),
        ]
    )
    session = RLMSession(client, sandbox_mode="subprocess", sandbox_timeout_s=15)
    answer, _cites, no_answer = session.run("q", docs)

    assert answer == "best-effort answer"
    assert no_answer is False
    _assert_tool_uses_answered(client.chat_calls[-1]["messages"])


def test_inprocess_mode_default_matches_existing_behaviour():
    # the default mode is in-process and identical to the bare RLMSession path.
    docs = FakeDocs({"ACME_2020": ["revenue was 100", "costs"]})
    code = "final('100', filings=['ACME_2020'], pages=[0])"
    client = FakeClient([_tool_use(code)])
    session = RLMSession(client, sandbox_mode="inprocess")
    answer, cites, no_answer = session.run("what was revenue?", docs)
    assert answer == "100"
    assert no_answer is False
    assert cites[0]["filing"] == "ACME_2020"
    assert "revenue was 100" in cites[0]["content"]
