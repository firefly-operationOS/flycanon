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

"""The RLM engine's provider seam: what routes where, and what is refused.

No network. ``build_rlm_client`` is exercised for its routing decision and
its refusals, which are the part an operator meets first.
"""

from __future__ import annotations

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.query.rlm.azure_client import AzureOpenAIChatClient
from flycanon.core.services.query.rlm.chat import (
    SUPPORTED_PROVIDERS,
    RlmChatClient,
    TokenLedger,
    UnsupportedRlmProvider,
    build_rlm_client,
    parse_model_ref,
    parse_price_table,
)
from flycanon.core.services.query.rlm.client import AnthropicClient

ENDPOINT = "https://canon-test.openai.azure.com"


def _azure_settings(**overrides) -> CanonSettings:
    base = {
        "rlm_root_model": "azure:gpt-5-4-canon",
        "rlm_sub_model": "azure:gpt-5-4-canon",
        "azure_openai_endpoint": ENDPOINT,
        "azure_openai_api_key": "azure-key",
        "azure_model_prices": "",
    }
    base.update(overrides)
    return CanonSettings(**base)


# ---------------------------------------------------------------------------
# parse_model_ref
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "provider", "name"),
    [
        ("anthropic:claude-sonnet-5", "anthropic", "claude-sonnet-5"),
        ("ANTHROPIC:claude-sonnet-5", "anthropic", "claude-sonnet-5"),
        ("claude-sonnet-5", "anthropic", "claude-sonnet-5"),  # bare id stays Anthropic
        ("azure:gpt-5-4-canon", "azure", "gpt-5-4-canon"),
        ("AZURE:gpt-5-4-canon", "azure", "gpt-5-4-canon"),
        # the embedding path accepts both spellings; so does this one
        ("azure-openai:gpt-5-4-canon", "azure", "gpt-5-4-canon"),
    ],
)
def test_parse_model_ref_resolves_every_supported_spelling(raw: str, provider: str, name: str):
    ref = parse_model_ref(raw, setting="FLYCANON_RLM_ROOT_MODEL")
    assert (ref.provider, ref.name) == (provider, name)


@pytest.mark.parametrize("raw", ["openai:gpt-5.2", "bedrock:eu.anthropic.claude-sonnet-5-v1:0", "vertex:x"])
def test_parse_model_ref_refuses_an_unknown_prefix_by_name(raw: str):
    """The defect this seam replaces was a SILENT strip. The refusal is loud.

    It must name the setting, the prefix it was handed, and every prefix
    that works -- an error that only says "unsupported" leaves the operator
    guessing at the spelling.
    """
    with pytest.raises(UnsupportedRlmProvider) as exc:
        parse_model_ref(raw, setting="FLYCANON_RLM_ROOT_MODEL")
    message = str(exc.value)
    assert "FLYCANON_RLM_ROOT_MODEL" in message
    assert raw.split(":", 1)[0] in message
    for provider in SUPPORTED_PROVIDERS:
        assert provider in message


def test_parse_model_ref_refuses_a_prefix_with_no_model():
    with pytest.raises(UnsupportedRlmProvider, match="no model after it"):
        parse_model_ref("azure:", setting="FLYCANON_RLM_SUB_MODEL")


# ---------------------------------------------------------------------------
# build_rlm_client
# ---------------------------------------------------------------------------


def test_anthropic_settings_build_the_anthropic_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = build_rlm_client(CanonSettings(rlm_root_model="anthropic:claude-sonnet-5"))
    assert isinstance(client, AnthropicClient)
    assert isinstance(client, RlmChatClient)
    assert client.root_model == "claude-sonnet-5"


def test_azure_settings_build_the_azure_client():
    """``azure:<deployment>`` reaches Azure instead of meaning something else.

    Before this release the same configuration constructed the Anthropic
    client, dropped the prefix and POSTed ``{"model": "gpt-5-4-canon"}`` to
    ``api.anthropic.com``.
    """
    client = build_rlm_client(_azure_settings())
    assert isinstance(client, AzureOpenAIChatClient)
    assert isinstance(client, RlmChatClient)
    assert client.root_model == "gpt-5-4-canon"
    assert client.sub_model == "gpt-5-4-canon"


def test_an_unknown_prefix_is_refused_at_boot():
    with pytest.raises(UnsupportedRlmProvider) as exc:
        build_rlm_client(CanonSettings(rlm_root_model="openai:gpt-5.2"))
    assert "FLYCANON_RLM_ROOT_MODEL" in str(exc.value)


def test_an_unknown_sub_model_prefix_names_the_sub_setting(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(UnsupportedRlmProvider) as exc:
        build_rlm_client(
            CanonSettings(rlm_root_model="anthropic:claude-sonnet-5", rlm_sub_model="openai:gpt-5.2")
        )
    assert "FLYCANON_RLM_SUB_MODEL" in str(exc.value)


def test_a_root_and_sub_on_two_providers_is_refused(monkeypatch):
    """One client, one endpoint, one credential -- so one provider.

    Refused in a sentence rather than half-served by a dispatching client
    nobody asked for.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(UnsupportedRlmProvider) as exc:
        build_rlm_client(_azure_settings(rlm_sub_model="anthropic:claude-haiku-4-5"))
    message = str(exc.value)
    assert "FLYCANON_RLM_ROOT_MODEL" in message and "FLYCANON_RLM_SUB_MODEL" in message
    assert "same provider" in message


# ---------------------------------------------------------------------------
# price table
# ---------------------------------------------------------------------------


def test_price_table_parses_entries_and_tolerates_whitespace():
    table = parse_price_table(" a-deploy=1.25/10.00 , b-deploy=2/4 ", setting="X")
    assert table == {"a-deploy": (1.25, 10.0), "b-deploy": (2.0, 4.0)}


def test_price_table_is_empty_when_unset():
    assert parse_price_table("", setting="X") == {}


@pytest.mark.parametrize("raw", ["a-deploy", "a-deploy=1.25", "a-deploy=x/y", "=1/2", "a=-1/2"])
def test_price_table_refuses_a_malformed_entry(raw: str):
    """Refused, not skipped: a table that drops the row you mistyped bills wrong."""
    with pytest.raises(ValueError, match="X:"):
        parse_price_table(raw, setting="X")


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


def test_ledger_prices_known_models_and_reports_unpriced_ones():
    ledger = TokenLedger({"priced": (3.0, 15.0)})
    ledger.record("priced", input_tokens=1_000_000, output_tokens=1_000_000)
    ledger.record("unpriced", input_tokens=2_000, output_tokens=1_000)

    totals = ledger.totals()
    assert totals["input_tokens"] == 1_002_000
    assert totals["output_tokens"] == 1_001_000
    assert totals["estimated_cost_usd"] == pytest.approx(18.0)
    # The unpriced model's TOKENS are counted exactly; only its cost is zero,
    # and the ledger says which model that was.
    assert totals["by_model"]["unpriced"] == {"input": 2_000, "output": 1_000}
    assert ledger.unpriced() == ["unpriced"]


def test_ledger_reset_clears_everything():
    ledger = TokenLedger({})
    ledger.record("m", input_tokens=5, output_tokens=2)
    ledger.reset()
    assert ledger.totals() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "by_model": {},
    }
    assert ledger.unpriced() == []
