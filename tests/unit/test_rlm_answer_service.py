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
that returns a scripted ``(answer, citations)`` without an Anthropic
call. There is no ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import pytest

from flycanon.config import get_settings
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

    def __init__(self, answer: str, citations: list[dict]):
        self._answer = answer
        self._citations = citations

    def run(self, question, docs):
        FakeSession.last_question = question
        return self._answer, self._citations


def _docs(pages: dict[str, list[str]], sources: dict[str, SourceMeta]) -> CanonDocStore:
    return CanonDocStore(pages, sources)


def _service(corpus_builder) -> RLMAnswerService:
    # The client is never touched (the session is faked), so a sentinel
    # object is enough -- no real AnthropicClient, no API key.
    return RLMAnswerService(
        corpus_builder=corpus_builder,
        client=object(),
        settings=get_settings(),
    )


def _request(**kw) -> AnswerRequest:
    return AnswerRequest(question=kw.pop("question", "What is the revenue?"), **kw)


@pytest.mark.asyncio
async def test_empty_corpus_returns_no_answer():
    builder = FakeCorpusBuilder(_docs({}, {}))
    service = _service(builder)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    assert resp.citations == []
    assert "no documents" in resp.answer.lower()
    assert resp.model == get_settings().rlm_root_model
    assert resp.elapsed_ms >= 0
    # scope is threaded into the corpus build
    assert builder.calls[0]["tenant_id"] == "t1"
    assert builder.calls[0]["workspace_id"] == "w1"


@pytest.mark.asyncio
async def test_normal_run_maps_citations(monkeypatch):
    pages = {"acme-10k": ["page zero text", "revenue was 5M", "page two"]}
    sources = {"acme-10k": SourceMeta(source_id="src-1", filename="acme.pdf", title="ACME 10-K", kind="pdf")}
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
    sources = {"acme-10k": SourceMeta(source_id="src-1", filename=None, title=None, kind="pdf")}
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
    sources = {"acme-10k": SourceMeta(source_id="src-1", filename=None, title=None, kind="pdf")}
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession("The documents do not contain this.", [])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    resp = await service.answer(_request(), tenant_id="t1", workspace_id="w1")

    assert resp.no_answer is True
    assert resp.citations == []


@pytest.mark.asyncio
async def test_prior_turns_prepended_to_question(monkeypatch):
    pages = {"acme-10k": ["a"]}
    sources = {"acme-10k": SourceMeta(source_id="src-1", filename=None, title=None, kind="pdf")}
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
    sources = {"acme-10k": SourceMeta(source_id="src-1", filename=None, title=None, kind="pdf")}
    builder = FakeCorpusBuilder(_docs(pages, sources))
    service = _service(builder)

    fake = FakeSession("ok", [])
    monkeypatch.setattr(svc_mod, "RLMSession", lambda *a, **k: fake)

    await service.answer(_request(question="Plain question?"), tenant_id="t1", workspace_id="w1")

    assert FakeSession.last_question == "Plain question?"
