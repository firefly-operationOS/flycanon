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

"""Unit tests for :class:`RLMAnswerService`.

No network / LLM: the corpus builder is a stub returning an in-memory
:class:`CanonDocStore`, and the RLM session is monkeypatched to a fake
that returns a scripted ``(answer, citations, no_answer)`` without an
Anthropic call. There is no ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings, get_settings
from flycanon.core.services.ingestion.loaders import default_registry
from flycanon.core.services.query import rlm_answer_service as svc_mod
from flycanon.core.services.query.rlm.corpus import CanonDocStore, SourceMeta
from flycanon.core.services.query.rlm_answer_service import RLMAnswerService
from flycanon.interfaces.dtos.query import AnswerRequest


class FakeCorpusBuilder:
    """Returns a canned :class:`CanonDocStore`, records the build kwargs."""

    def __init__(self, docs: CanonDocStore):
        self._docs = docs
        self.calls: list[dict] = []

    async def build(self, *, tenant_id, workspace_id, filters):
        self.calls.append({"tenant_id": tenant_id, "workspace_id": workspace_id, "filters": filters})
        return self._docs


class FakeSession:
    """Scripted RLM session: records the question, returns canned output."""

    last_question: str | None = None

    def __init__(self, answer: str, citations: list[dict], no_answer: bool = False):
        self._answer = answer
        self._citations = citations
        self._no_answer = no_answer

    def run(self, question, docs):
        FakeSession.last_question = question
        return self._answer, self._citations, self._no_answer


class FakeClient:
    """Stands in for the shared :class:`AnthropicClient`.

    ``fork()`` returns self (the session is faked, so no real calls happen)
    and ``token_totals()`` returns a scripted usage dict, mirroring the real
    per-query token snapshot the service records.
    """

    def __init__(self, usage: dict | None = None):
        self._usage = usage or {
            "input_tokens": 1200,
            "output_tokens": 340,
            "estimated_cost_usd": 0.0072,
            "by_model": {"claude-sonnet-4-6": {"input": 1200, "output": 340}},
        }
        self.forks = 0

    def fork(self) -> FakeClient:
        self.forks += 1
        return self

    def token_totals(self) -> dict:
        return self._usage


class FakeCostService:
    """Records every :meth:`record` call; can be made to raise on demand."""

    def __init__(self, raises: bool = False):
        self.calls: list[dict] = []
        self._raises = raises

    async def record(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("billing backend down")
        return None


class _NullObjectStore:
    """ObjectStore that is never fetched: these tests pre-seed the page memo."""

    def get_sync(self, key: str) -> bytes:  # pragma: no cover -- never reached
        raise AssertionError(f"unexpected fetch for {key!r}")


def _docs(pages: dict[str, list[str]], sources: dict[str, SourceMeta]) -> CanonDocStore:
    # The store is lazy, but these answer-service tests work from fixed page
    # lists, so pre-seed the per-key memo and let the object store stay unused.
    store = CanonDocStore(sources, object_store=_NullObjectStore(), registry=default_registry())
    store._pages.update(pages)
    return store


def _service(
    corpus_builder,
    *,
    client: FakeClient | None = None,
    cost_service: FakeCostService | None = None,
) -> RLMAnswerService:
    # The real client is never networked (the session is faked); a FakeClient
    # supplies the fork()/token_totals() surface the service now relies on.
    return RLMAnswerService(
        corpus_builder=corpus_builder,
        client=client or FakeClient(),
        settings=get_settings(),
        cost_service=cost_service or FakeCostService(),
    )


def _request(**kw) -> AnswerRequest:
    return AnswerRequest(question=kw.pop("question", "What is the revenue?"), **kw)


@pytest.mark.asyncio
async def test_empty_corpus_returns_no_answer():
    builder = FakeCorpusBuilder(_docs({}, {}))
    cost = FakeCostService()
    service = _service(builder, cost_service=cost)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    assert resp.citations == []
    assert "no documents" in resp.answer.lower()
    assert resp.model == get_settings().rlm_root_model
    assert resp.elapsed_ms >= 0
    # scope is threaded into the corpus build
    assert builder.calls[0]["tenant_id"] == "t1"
    assert builder.calls[0]["workspace_id"] == "w1"
    # no LLM call happened on the empty-corpus path -> no cost event
    assert cost.calls == []


@pytest.mark.asyncio
async def test_normal_run_maps_citations(monkeypatch):
    pages = {"acme-10k": ["page zero text", "revenue was 5M", "page two"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename="acme.pdf",
            title="ACME 10-K",
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession(
        "Revenue was 5M.",
        [{"filing": "acme-10k", "page": 1, "content": "revenue was 5M"}],
    )
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.answer == "Revenue was 5M."
    assert resp.no_answer is False
    assert resp.model == get_settings().rlm_root_model
    assert len(resp.citations) == 1
    cite = resp.citations[0]
    assert cite.source_id == "src-1"
    assert cite.page == 2  # engine 0-based page 1 -> Hit 1-based page 2
    assert cite.chunk_id == "src-1#p2"
    assert cite.content == "revenue was 5M"
    assert cite.source_filename == "acme.pdf"
    assert cite.source_title == "ACME 10-K"
    assert cite.source_kind == "pdf"
    assert cite.score == 1.0


@pytest.mark.asyncio
async def test_unresolved_citation_is_dropped(monkeypatch):
    pages = {"acme-10k": ["a", "b"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession(
        "Answer.",
        [
            {"filing": "acme-10k", "page": 0, "content": "a"},
            {"filing": "ghost-doc", "page": 0, "content": "x"},  # does not resolve
        ],
    )
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert len(resp.citations) == 1
    assert resp.citations[0].source_id == "src-1"
    assert resp.citations[0].page == 1  # 0-based 0 clamped to 1-based 1


@pytest.mark.asyncio
async def test_no_answer_when_not_found_and_no_citations(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    # engine flag is the default False -> the text-marker fallback decides
    fake = FakeSession("The documents do not contain this.", [], no_answer=False)
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    assert resp.citations == []


@pytest.mark.asyncio
async def test_engine_no_answer_flag_surfaces_even_without_markers(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    # answer text has none of the not-found markers, but the engine flagged it
    fake = FakeSession("Unable to determine the figure.", [], no_answer=True)
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    assert resp.citations == []


@pytest.mark.asyncio
async def test_engine_found_answer_is_not_no_answer(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    # engine flag False and no marker in the text -> not a no-answer, even with
    # no citations (a best-effort plain answer)
    fake = FakeSession("The figure is 5M.", [], no_answer=False)
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is False


@pytest.mark.asyncio
async def test_blank_answer_is_flagged_no_answer(monkeypatch):
    # The CodeAct loop can exhaust ``rlm_max_iters`` without ever calling
    # ``final()``; the engine then returns an empty string with the default
    # ``found`` flag (no_answer=False). That degenerate non-answer must NOT
    # surface as a valid answer -- callers cannot otherwise detect the failure.
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession("", [], no_answer=False)
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    # a non-answer carries an explanatory note, never an empty string
    assert resp.answer.strip() != ""
    assert resp.citations == []


@pytest.mark.asyncio
async def test_whitespace_only_answer_is_flagged_no_answer(monkeypatch):
    # Same degenerate case, but the engine emitted only whitespace.
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession("   \n\t  ", [{"filing": "acme-10k", "page": 0, "content": "a"}], no_answer=False)
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    assert resp.answer.strip() != ""
    # citations are dropped for a non-answer -- they cannot support an answer
    # that was never produced
    assert resp.citations == []


@pytest.mark.asyncio
async def test_prior_turns_prepended_to_question(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession("ok", [])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    await service.answer(
        _request(question="And the net margin?"),
        prior_turns=[("What is the revenue?", "Revenue was 5M.")],
        tenant_id="t1",
        workspace_id="w1",
    )

    assert "Previous turns:" in FakeSession.last_question
    assert "What is the revenue?" in FakeSession.last_question
    assert "Revenue was 5M." in FakeSession.last_question
    assert "Current question: And the net margin?" in FakeSession.last_question


@pytest.mark.asyncio
async def test_no_prior_turns_leaves_question_untouched(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession("ok", [])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    await service.answer(_request(question="Plain question?"), tenant_id="t1", workspace_id="w1")

    assert FakeSession.last_question == "Plain question?"


@pytest.mark.asyncio
async def test_normal_run_records_one_cost_event(monkeypatch):
    pages = {"acme-10k": ["a", "revenue was 5M"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename="acme.pdf",
            title="ACME 10-K",
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    client = FakeClient(
        {
            "input_tokens": 1200,
            "output_tokens": 340,
            "estimated_cost_usd": 0.0072,
            "by_model": {"claude-sonnet-4-6": {"input": 1200, "output": 340}},
        }
    )
    cost = FakeCostService()
    service = _service(builder, client=client, cost_service=cost)

    fake = FakeSession("Revenue was 5M.", [{"filing": "acme-10k", "page": 1, "content": "x"}])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    # the shared client is forked once per query
    assert client.forks == 1
    # exactly one cost event with the right attribution
    assert len(cost.calls) == 1
    call = cost.calls[0]
    assert call["agent_name"] == "flycanon-rlm-answerer"
    assert call["model"] == get_settings().rlm_root_model
    assert call["input_tokens"] == 1200
    assert call["output_tokens"] == 340
    assert call["cost_usd"] == 0.0072
    assert call["tenant_id"] == "t1"
    assert call["workspace_id"] == "w1"
    assert call["latency_ms"] == resp.elapsed_ms


@pytest.mark.asyncio
async def test_missing_scope_defaults_to_default(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename=None,
            title=None,
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    cost = FakeCostService()
    service = _service(builder, cost_service=cost)

    fake = FakeSession("ok", [])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    await service.answer(_request())

    assert cost.calls[0]["tenant_id"] == "default"
    assert cost.calls[0]["workspace_id"] == "default"


@pytest.mark.asyncio
async def test_cost_record_failure_does_not_break_answer(monkeypatch):
    pages = {"acme-10k": ["a", "b"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename="acme.pdf",
            title="ACME 10-K",
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    cost = FakeCostService(raises=True)
    service = _service(builder, cost_service=cost)

    fake = FakeSession("Revenue was 5M.", [{"filing": "acme-10k", "page": 1, "content": "x"}])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    # a billing-write failure must not propagate
    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.answer == "Revenue was 5M."
    assert resp.no_answer is False
    assert len(resp.citations) == 1
    # the record() was attempted (and raised, swallowed best-effort)
    assert len(cost.calls) == 1


@pytest.mark.asyncio
async def test_sandbox_settings_are_threaded_into_session(monkeypatch):
    pages = {"acme-10k": ["a", "b"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename="acme.pdf",
            title="ACME 10-K",
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    settings = CanonSettings(rlm_sandbox="subprocess", rlm_sandbox_timeout_s=45)
    service = RLMAnswerService(
        corpus_builder=builder,
        client=FakeClient(),
        settings=settings,
        cost_service=FakeCostService(),
    )

    captured: dict = {}

    def _capture(*_args, **kwargs):
        captured.update(kwargs)
        return FakeSession("Revenue was 5M.", [{"filing": "acme-10k", "page": 1, "content": "x"}])

    monkeypatch.setattr(svc_mod, "RLMSession", _capture)

    await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    # the config knobs reach the session that runs the REPL.
    assert captured["sandbox_mode"] == "subprocess"
    assert captured["sandbox_timeout_s"] == 45


@pytest.mark.asyncio
async def test_default_sandbox_mode_is_subprocess(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {
        "acme-10k": SourceMeta(
            source_id="src-1",
            filename="acme.pdf",
            title="ACME 10-K",
            kind="pdf",
            object_store_key="k1",
            content_sha256="sha-1",
        )
    }
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    captured: dict = {}

    def _capture(*_args, **kwargs):
        captured.update(kwargs)
        return FakeSession("ok", [{"filing": "acme-10k", "page": 0, "content": "a"}])

    monkeypatch.setattr(svc_mod, "RLMSession", _capture)

    await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    # default settings run the sandboxed subprocess (RLMSession is faked, so
    # no real child is spawned -- only the threaded mode value is asserted).
    assert captured["sandbox_mode"] == "subprocess"
    assert captured["sandbox_timeout_s"] == 30
