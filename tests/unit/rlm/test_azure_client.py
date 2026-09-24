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

"""Unit tests for the Azure OpenAI RLM client.

No network: the ``httpx.Client`` is faked, so what is asserted is the exact
wire body an Azure deployment would receive and the exact response shape the
CodeAct loop would read back. The last section drives a whole
:class:`RLMSession` over the fake, because the translation is only correct if
the session it feeds never notices which provider answered.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.azure import AzureConfigurationError
from flycanon.core.services.query.rlm.azure_client import AzureOpenAIChatClient
from flycanon.core.services.query.rlm.session import RLMSession

ENDPOINT = "https://canon-test.openai.azure.com"
ROOT = "gpt-5-4-root"
SUB = "gpt-5-4-sub"

PY_TOOL = [
    {
        "name": "python",
        "description": "Execute Python in the persistent REPL.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    }
]


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """Records POSTs and replays a queued list of responses.

    A queued ``dict`` is a 200 carrying that payload, which is what almost
    every case wants; a ``_FakeResponse`` states a status explicitly and an
    ``Exception`` is raised as a transport failure.
    """

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json, headers):  # noqa: A002 - mirror httpx signature
        self.calls.append({"url": url, "json": json, "headers": headers})
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _FakeResponse(200, nxt) if isinstance(nxt, dict) else nxt


def _settings(**overrides) -> CanonSettings:
    base = {
        "rlm_root_model": f"azure:{ROOT}",
        "rlm_sub_model": f"azure:{SUB}",
        "azure_openai_endpoint": ENDPOINT,
        "azure_openai_api_key": "azure-key",
        "azure_openai_api_version": "2026-05-01",
        "azure_auth": "api_key",
        "azure_model_prices": "",
    }
    base.update(overrides)
    return CanonSettings(**base)


def _text_reply(text: str, *, usage: dict | None = None) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 4},
    }


def _tool_reply(code: str, call_id: str, *, usage: dict | None = None) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "python", "arguments": json.dumps({"code": code})},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_construction_requires_the_endpoint(monkeypatch):
    """The same two settings the embedding path uses, validated the same way."""
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("FLYCANON_AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(AzureConfigurationError, match="FLYCANON_AZURE_OPENAI_ENDPOINT"):
        AzureOpenAIChatClient(_settings(azure_openai_endpoint=""), http_client=_FakeHttp([]))


def test_construction_requires_a_key_on_the_api_key_path():
    with pytest.raises(AzureConfigurationError, match="FLYCANON_AZURE_OPENAI_API_KEY"):
        AzureOpenAIChatClient(_settings(azure_openai_api_key=""), http_client=_FakeHttp([]))


def test_deployment_names_come_from_the_rlm_settings():
    client = AzureOpenAIChatClient(_settings(), http_client=_FakeHttp([]))
    assert (client.root_model, client.sub_model) == (ROOT, SUB)


# ---------------------------------------------------------------------------
# the request on the wire
# ---------------------------------------------------------------------------


def test_chat_raw_targets_the_root_deployment_with_the_api_version():
    http = _FakeHttp([_tool_reply("print(1)", "call_1")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw([{"role": "user", "content": "hi"}], "sys", PY_TOOL)

    call = http.calls[0]
    assert call["url"] == (f"{ENDPOINT}/openai/deployments/{ROOT}/chat/completions?api-version=2026-05-01")
    assert call["headers"]["api-key"] == "azure-key"
    assert "Authorization" not in call["headers"]


def test_the_system_prompt_becomes_the_leading_system_message():
    http = _FakeHttp([_tool_reply("print(1)", "call_1")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw([{"role": "user", "content": "hi"}], "you are the orchestrator", PY_TOOL)

    messages = http.calls[0]["json"]["messages"]
    assert messages[0] == {"role": "system", "content": "you are the orchestrator"}
    assert messages[1] == {"role": "user", "content": "hi"}


def test_tools_are_sent_as_openai_function_definitions():
    http = _FakeHttp([_tool_reply("print(1)", "call_1")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw([{"role": "user", "content": "hi"}], "sys", PY_TOOL)

    body = http.calls[0]["json"]
    assert body["tool_choice"] == "auto"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Execute Python in the persistent REPL.",
                # ``input_schema`` and ``parameters`` are the same schema, renamed
                "parameters": PY_TOOL[0]["input_schema"],
            },
        }
    ]


def test_the_budget_is_max_completion_tokens_and_no_sampling_knob_is_sent():
    """A deployment name says nothing about the model behind it.

    ``max_tokens`` and ``temperature`` are both 400s on the GPT-5 / o-series
    reasoning models, and the deployment name cannot tell us whether this is
    one of them, so neither is ever sent.
    """
    http = _FakeHttp([_tool_reply("print(1)", "call_1"), _text_reply("ok")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw([{"role": "user", "content": "hi"}], "sys", PY_TOOL, max_tokens=1500)
    client.complete("q", max_tokens=1000)

    assert http.calls[0]["json"]["max_completion_tokens"] == 1500
    assert http.calls[1]["json"]["max_completion_tokens"] == 1000
    for call in http.calls:
        body = call["json"]
        assert "max_tokens" not in body
        assert "temperature" not in body and "top_p" not in body and "top_k" not in body
        # Anthropic's explicit cache breakpoint has no Azure equivalent; the
        # system prompt is a plain string here, not a cache_control block.
        assert isinstance(body["messages"][0]["content"], str)


def test_a_tool_less_turn_sends_no_tools_field():
    """The forced-final turn: no tools, so the model must answer in text."""
    http = _FakeHttp([_text_reply("the allowance is 70 euros")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw([{"role": "user", "content": "hi"}], "sys", [], max_tokens=4096)

    body = http.calls[0]["json"]
    assert "tools" not in body and "tool_choice" not in body


def test_complete_targets_the_sub_deployment_and_omits_the_system_message():
    http = _FakeHttp([_text_reply("Hello world")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)

    assert client.complete("q") == "Hello world"
    assert f"/deployments/{SUB}/" in http.calls[0]["url"]
    assert http.calls[0]["json"]["messages"] == [{"role": "user", "content": "q"}]


def test_complete_with_a_system_prompt_prepends_it():
    http = _FakeHttp([_text_reply("ok")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.complete("q", system="be brief")
    assert http.calls[0]["json"]["messages"][0] == {"role": "system", "content": "be brief"}


def test_a_per_call_override_names_a_deployment_and_refuses_another_provider():
    http = _FakeHttp([_text_reply("ok")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)

    client.complete("q", model="azure:other-deployment")
    assert "/deployments/other-deployment/" in http.calls[0]["url"]

    with pytest.raises(ValueError, match="this RLM run is on Azure"):
        client.complete("q", model="anthropic:claude-sonnet-5")


# ---------------------------------------------------------------------------
# the transcript, in both directions
# ---------------------------------------------------------------------------


def test_the_assistant_turn_and_its_tool_results_survive_the_round_trip():
    """The loop's own transcript vocabulary, translated and translated back.

    ``RLMSession`` appends the provider's content blocks to ``messages`` and
    answers each ``tool_use`` with a ``tool_result``. Both have to arrive as
    the Chat Completions shapes, or the second turn is a 400.
    """
    http = _FakeHttp([_text_reply("done")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    transcript = [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me look"},
                {"type": "tool_use", "id": "call_1", "name": "python", "input": {"code": "print(1)"}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "1\n"}]},
    ]
    client.chat_raw(transcript, "sys", PY_TOOL)

    messages = http.calls[0]["json"]["messages"]
    assert messages[1] == {"role": "user", "content": "question"}
    assert messages[2] == {
        "role": "assistant",
        "content": "let me look",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "python", "arguments": '{"code": "print(1)"}'},
            }
        ],
    }
    # One ``tool`` message per answered call -- that is how Chat Completions
    # closes a tool call, exactly as a tool_result does at Anthropic.
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": "1\n"}


def test_an_error_tool_result_still_answers_the_call():
    """The dead-sandbox path emits ``is_error`` results; they must not vanish."""
    http = _FakeHttp([_text_reply("done")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call_9", "name": "python", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_9",
                        "content": "sandbox terminated",
                        "is_error": True,
                    }
                ],
            },
        ],
        "sys",
        PY_TOOL,
    )
    assert http.calls[0]["json"]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_9",
        "content": "sandbox terminated",
    }


def test_an_assistant_turn_with_neither_text_nor_calls_is_not_null_content():
    """``content: null`` is a 400 on a turn that carries no tool calls."""
    http = _FakeHttp([_text_reply("done")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.chat_raw([{"role": "user", "content": "q"}, {"role": "assistant", "content": []}], "", PY_TOOL)
    assert http.calls[0]["json"]["messages"][-1] == {"role": "assistant", "content": ""}


def test_the_response_arrives_as_anthropic_content_blocks():
    http = _FakeHttp(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "thinking out loud",
                            "tool_calls": [
                                {
                                    "id": "call_7",
                                    "type": "function",
                                    "function": {
                                        "name": "python",
                                        "arguments": '{"code": "final(\'70\')"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ]
    )
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    response = client.chat_raw([{"role": "user", "content": "hi"}], "sys", PY_TOOL)

    assert response["stop_reason"] == "tool_use"
    assert response["content"] == [
        {"type": "text", "text": "thinking out loud"},
        {"type": "tool_use", "id": "call_7", "name": "python", "input": {"code": "final('70')"}},
    ]


@pytest.mark.parametrize(
    ("finish_reason", "stop_reason"),
    [("tool_calls", "tool_use"), ("stop", "end_turn"), ("length", "max_tokens")],
)
def test_finish_reason_is_renamed_to_the_transcript_vocabulary(finish_reason: str, stop_reason: str):
    http = _FakeHttp(
        [
            {
                "choices": [{"message": {"content": "x"}, "finish_reason": finish_reason}],
                "usage": {},
            }
        ]
    )
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    assert client.chat_raw([{"role": "user", "content": "hi"}], "", [])["stop_reason"] == stop_reason


def test_a_filtered_prompt_is_an_error_not_an_empty_answer():
    """A 200 with no choices is what a prompt content filter returns.

    Translated to an empty content list it would reach the loop as "the
    model answered in plain text" and leave the user with a blank answer
    and no error anywhere -- the exact failure mode this whole change is
    about.
    """
    http = _FakeHttp([{"choices": [], "prompt_filter_results": [{"content_filter_results": {}}]}])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    with pytest.raises(RuntimeError, match="no choices for deployment"):
        client.chat_raw([{"role": "user", "content": "hi"}], "", PY_TOOL)


def test_unparseable_tool_arguments_are_reported_not_guessed(caplog):
    """A malformed call becomes an empty one, loudly, so the loop can recover."""
    http = _FakeHttp(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_3",
                                    "type": "function",
                                    "function": {"name": "python", "arguments": "{not json"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {},
            }
        ]
    )
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    with caplog.at_level(logging.WARNING):
        response = client.chat_raw([{"role": "user", "content": "hi"}], "", PY_TOOL)

    assert response["content"] == [{"type": "tool_use", "id": "call_3", "name": "python", "input": {}}]
    assert "not JSON" in caplog.text and ROOT in caplog.text


# ---------------------------------------------------------------------------
# retries, accounting, cost
# ---------------------------------------------------------------------------


def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setattr("flycanon.core.services.query.rlm.azure_client.time.sleep", lambda _s: None)
    http = _FakeHttp([_FakeResponse(429, text="slow down"), _text_reply("done")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    assert client.complete("q") == "done"
    assert len(http.calls) == 2


def test_retry_on_httpx_error(monkeypatch):
    monkeypatch.setattr("flycanon.core.services.query.rlm.azure_client.time.sleep", lambda _s: None)
    http = _FakeHttp([httpx.ConnectError("boom"), _text_reply("ok")])
    assert AzureOpenAIChatClient(_settings(), http_client=http).complete("q") == "ok"


def test_a_non_retriable_status_fails_immediately_and_names_the_deployment(monkeypatch):
    monkeypatch.setattr("flycanon.core.services.query.rlm.azure_client.time.sleep", lambda _s: None)
    http = _FakeHttp([_FakeResponse(404, text="DeploymentNotFound")])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    with pytest.raises(RuntimeError, match=f"deployment '{SUB}'"):
        client.complete("q")
    assert len(http.calls) == 1


def test_usage_is_read_from_azures_own_spelling():
    """``prompt_tokens`` / ``completion_tokens``, not ``input_tokens`` / ``output_tokens``.

    Reading the Anthropic names here would have produced a ledger of zeroes
    that looked exactly like a client nobody called.
    """
    http = _FakeHttp([_text_reply("x", usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000})])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.complete("q")

    totals = client.token_totals()
    assert totals["input_tokens"] == 1_000_000
    assert totals["output_tokens"] == 500_000
    assert totals["by_model"][SUB] == {"input": 1_000_000, "output": 500_000}


def test_an_unpriced_deployment_counts_tokens_and_says_the_cost_is_unknown(caplog):
    """Zero cost is reported, never silently.

    Azure rates are per deployment and per agreement, so flycanon cannot
    infer them; the warning names the deployment and the setting that fixes
    the bill.
    """
    http = _FakeHttp([_text_reply("x", usage={"prompt_tokens": 1_000, "completion_tokens": 100})])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    with caplog.at_level(logging.WARNING):
        client.complete("q")

    assert client.token_totals()["estimated_cost_usd"] == 0.0
    assert client.token_totals()["input_tokens"] == 1_000
    assert "FLYCANON_AZURE_MODEL_PRICES" in caplog.text and SUB in caplog.text


def test_a_configured_price_bills_the_azure_path():
    http = _FakeHttp([_text_reply("x", usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})])
    client = AzureOpenAIChatClient(
        _settings(azure_model_prices=f"{SUB}=1.25/10.00,{ROOT}=2.50/20.00"), http_client=http
    )
    client.complete("q")
    assert client.token_totals()["estimated_cost_usd"] == pytest.approx(1.25 + 10.00)


def test_fork_isolates_tokens_but_shares_the_pool():
    http = _FakeHttp([])
    parent = AzureOpenAIChatClient(_settings(), http_client=http)
    parent._record_usage(ROOT, {"prompt_tokens": 100, "completion_tokens": 50})

    child = parent.fork()

    assert child is not parent
    assert child._http is parent._http
    assert child.token_totals()["input_tokens"] == 0
    assert parent.token_totals()["input_tokens"] == 100


def test_reset_tokens_clears():
    http = _FakeHttp([_text_reply("x", usage={"prompt_tokens": 5, "completion_tokens": 2})])
    client = AzureOpenAIChatClient(_settings(), http_client=http)
    client.complete("q")
    client.reset_tokens()
    assert client.token_totals() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "by_model": {},
    }


def test_a_fork_reuses_the_credential_rather_than_building_a_new_one(monkeypatch):
    """One ``DefaultAzureCredential`` per process, not one per forked client.

    The self-consistency path forks a client per run; a new credential per
    fork would mean a cold token acquisition per run.
    """
    built = []

    def _provider_factory():
        built.append(1)
        return lambda: "entra-token"

    monkeypatch.setattr(
        "flycanon.core.services.query.rlm.azure_client.entra_token_provider", _provider_factory
    )
    parent = AzureOpenAIChatClient(
        _settings(azure_auth="managed_identity", azure_openai_api_key=""), http_client=_FakeHttp([])
    )
    child = parent.fork()

    assert len(built) == 1
    assert child._token_provider is parent._token_provider


def test_managed_identity_sends_a_bearer_token_and_no_api_key(monkeypatch):
    monkeypatch.setattr(
        "flycanon.core.services.query.rlm.azure_client.entra_token_provider",
        lambda: lambda: "entra-token",
    )
    http = _FakeHttp([_text_reply("ok")])
    client = AzureOpenAIChatClient(
        _settings(azure_auth="managed_identity", azure_openai_api_key=""), http_client=http
    )
    client.complete("q")

    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer entra-token"
    assert "api-key" not in headers


# ---------------------------------------------------------------------------
# the engine, end to end, on Azure
# ---------------------------------------------------------------------------


class _Docs:
    """The smallest DocCorpus: one filing, one page."""

    def __init__(self, page: str):
        self._page = page
        self.accessed: list[str] = []

    def keys(self):
        return ["POLICY"]

    def __getitem__(self, key: str) -> str:
        return self._page

    def __contains__(self, key: object) -> bool:
        return key == "POLICY"

    def pages(self, key: str) -> list[str]:
        return [self._page]

    def npages(self, key: str) -> int:
        return 1


def test_a_whole_codeact_session_runs_on_azure():
    """The session never learns which provider answered.

    The loop appends content blocks, answers tool calls, and ends on
    ``final(...)`` -- all of it over the Chat Completions wire.
    """
    http = _FakeHttp(
        [
            _tool_reply("print(docs['POLICY'])", "call_1"),
            _tool_reply("final('70 euros', filings=['POLICY'], pages=[0])", "call_2"),
        ]
    )
    client = AzureOpenAIChatClient(_settings(), http_client=http)

    answer, citations, no_answer = RLMSession(client).run("meal allowance?", _Docs("70 euros a day"))

    assert (answer, no_answer) == ("70 euros", False)
    assert citations[0]["filing"] == "POLICY"
    # Both root turns went to the root deployment, and the second one carried
    # the first turn's tool call and its answer.
    assert all(f"/deployments/{ROOT}/" in call["url"] for call in http.calls)
    second_turn = http.calls[1]["json"]["messages"]
    assert second_turn[2]["tool_calls"][0]["id"] == "call_1"
    assert second_turn[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "70 euros a day\n",
    }


def test_a_repl_sub_call_goes_to_the_sub_deployment():
    """``llm()`` from model-written code is the sub model's one job."""
    http = _FakeHttp(
        [
            _tool_reply("print(llm('extract the number'))", "call_1"),
            _text_reply("extracted: 70"),  # the llm() sub-call
            _tool_reply("final('70', filings=['POLICY'])", "call_2"),
        ]
    )
    client = AzureOpenAIChatClient(_settings(), http_client=http)

    answer, _citations, _no_answer = RLMSession(client).run("q", _Docs("70"))

    assert answer == "70"
    deployments = [call["url"].split("/deployments/")[1].split("/")[0] for call in http.calls]
    assert deployments == [ROOT, SUB, ROOT]
    assert "tools" not in http.calls[1]["json"]
    assert client.token_totals()["by_model"].keys() == {ROOT, SUB}


def test_the_forced_final_turn_runs_tool_less_on_azure():
    """Out of turns: a tool-less turn, so the model must answer in text."""
    http = _FakeHttp(
        [
            _tool_reply("print('still reading')", "call_1"),
            _text_reply("The allowance is 70 euros a day."),
        ]
    )
    client = AzureOpenAIChatClient(_settings(), http_client=http)

    answer, _citations, no_answer = RLMSession(client, max_iters=1).run("q", _Docs("70"))

    assert answer == "The allowance is 70 euros a day."
    assert no_answer is False
    forced = http.calls[-1]["json"]
    assert "tools" not in forced
    assert forced["max_completion_tokens"] == 4096
