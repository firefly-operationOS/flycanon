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

"""Azure as a first-class provider: configuration, capabilities and prices.

Azure used to be dispatched but not configured -- a bare
``os.environ.get("AZURE_OPENAI_ENDPOINT", "")`` whose empty default failed
inside the SDK, an api-version pinned at ``2024-02-01`` and unreachable from
flycanon, and ``dimensions`` forwarded to models that answer 400 to it. Each
test below pins one of those closed.
"""

from __future__ import annotations

from typing import Any

import pytest

from flycanon.config import CanonSettings
from flycanon.core.services.embeddings.azure import (
    COGNITIVE_SERVICES_SCOPE,
    AzureConfigurationError,
    FlycanonAzureEmbedder,
    require_azure_configuration,
)
from flycanon.core.services.embeddings.embedding_service import _build_embedder
from flycanon.core.services.embeddings.model_capabilities import (
    UNKNOWN,
    UnsupportedDimensionsError,
    capabilities_for,
    validate_dimensions,
)
from flycanon.core.services.embeddings.prices import price_for, render_cost

_ENDPOINT = "https://resource.openai.azure.com"


class TestConfiguration:
    def test_a_complete_key_configuration_passes(self) -> None:
        require_azure_configuration(endpoint=_ENDPOINT, api_version="2026-05-01", api_key="k", auth="api_key")

    def test_a_missing_endpoint_fails_at_config_time_naming_the_setting(self) -> None:
        with pytest.raises(AzureConfigurationError) as exc:
            require_azure_configuration(endpoint="", api_version="2026-05-01", api_key="k", auth="api_key")
        message = str(exc.value)
        assert "FLYCANON_AZURE_OPENAI_ENDPOINT" in message
        assert "AZURE_OPENAI_ENDPOINT" in message

    def test_a_plaintext_endpoint_is_refused(self) -> None:
        with pytest.raises(AzureConfigurationError, match="https-only"):
            require_azure_configuration(
                endpoint="http://resource.openai.azure.com",
                api_version="2026-05-01",
                api_key="k",
                auth="api_key",
            )

    def test_an_empty_api_version_is_refused(self) -> None:
        with pytest.raises(AzureConfigurationError, match="API_VERSION"):
            require_azure_configuration(endpoint=_ENDPOINT, api_version="", api_key="k", auth="api_key")

    def test_a_missing_key_on_the_key_path_names_the_alternative(self) -> None:
        with pytest.raises(AzureConfigurationError) as exc:
            require_azure_configuration(
                endpoint=_ENDPOINT, api_version="2026-05-01", api_key="", auth="api_key"
            )
        assert "FLYCANON_AZURE_AUTH=managed_identity" in str(exc.value)

    def test_managed_identity_needs_no_key(self) -> None:
        require_azure_configuration(
            endpoint=_ENDPOINT, api_version="2026-05-01", api_key="", auth="managed_identity"
        )

    def test_the_settings_carry_the_api_version_flycanon_could_not_reach_before(self) -> None:
        # Pinned at 2024-02-01 inside the framework until 26.8.0.
        assert CanonSettings().azure_openai_api_version == "2026-05-01"

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("managed_identity", "managed_identity"),
            ("managed-identity", "managed_identity"),
            ("MANAGED_IDENTITY", "managed_identity"),
            ("api_key", "api_key"),
            ("", "api_key"),
            ("typo", "api_key"),
        ],
    )
    def test_azure_auth_normalises_to_the_visible_failure(self, value: str, expected: str) -> None:
        """Anything unrecognised resolves to the key path, which fails on a
        missing key, rather than to an identity that is not there."""
        assert CanonSettings(azure_auth=value).azure_auth == expected

    def test_the_builder_refuses_an_azure_model_without_an_endpoint(self) -> None:
        with pytest.raises(AzureConfigurationError):
            _build_embedder(
                provider="azure",
                model="dep",
                dimensions=1536,
                batch_size=8,
                settings=CanonSettings(azure_openai_endpoint="", azure_openai_api_key="k"),
            )

    def test_the_builder_reaches_the_sdk_with_endpoint_version_and_deployment(self) -> None:
        embedder = _build_embedder(
            provider="azure",
            model="my-emb-3-large-deploy",
            dimensions=3072,
            batch_size=8,
            settings=CanonSettings(
                azure_openai_endpoint=_ENDPOINT,
                azure_openai_api_version="2026-05-01",
                azure_openai_api_key="k",
            ),
        )
        assert isinstance(embedder, FlycanonAzureEmbedder)
        client: Any = embedder._client
        assert str(client.base_url).startswith(_ENDPOINT)
        assert client._api_version == "2026-05-01"
        # The Azure ``model=`` is the DEPLOYMENT name, not the model name.
        assert embedder._model == "my-emb-3-large-deploy"

    def test_azure_openai_is_an_alias(self) -> None:
        embedder = _build_embedder(
            provider="azure-openai",
            model="dep",
            dimensions=1536,
            batch_size=8,
            settings=CanonSettings(azure_openai_endpoint=_ENDPOINT, azure_openai_api_key="k"),
        )
        assert isinstance(embedder, FlycanonAzureEmbedder)

    def test_managed_identity_uses_a_token_provider_and_no_key(self) -> None:
        calls: list[str] = []

        def _provider() -> str:
            calls.append("token")
            return "bearer-token"

        embedder = FlycanonAzureEmbedder(
            "dep",
            3072,
            azure_endpoint=_ENDPOINT,
            api_version="2026-05-01",
            token_provider=_provider,
        )
        client: Any = embedder._client
        assert client._azure_ad_token_provider is _provider
        assert COGNITIVE_SERVICES_SCOPE == "https://cognitiveservices.azure.com/.default"


class TestCapabilities:
    @pytest.mark.parametrize(
        "model,width",
        [
            ("text-embedding-3-small", 1536),
            ("text-embedding-3-large", 3072),
            ("text-embedding-ada-002", 1536),
            ("nomic-embed-text", 768),
        ],
    )
    def test_native_widths(self, model: str, width: int) -> None:
        assert capabilities_for(model).native_dimensions == width

    def test_a_deployment_named_after_a_model_resolves_to_it(self) -> None:
        """On Azure the id is a deployment name an operator chooses."""
        assert capabilities_for("prod-text-embedding-3-large").native_dimensions == 3072

    def test_an_unrecognised_deployment_asserts_nothing(self) -> None:
        """Guessing would be worse than not knowing: a table that refuses a
        legitimate deployment because of its name is one to be worked around."""
        assert capabilities_for("prod-emb") is UNKNOWN
        validate_dimensions(provider="azure", model="prod-emb", dimensions=1234)

    def test_ada_002_refuses_a_dimensions_parameter(self) -> None:
        with pytest.raises(UnsupportedDimensionsError) as exc:
            validate_dimensions(provider="azure", model="text-embedding-ada-002", dimensions=3072)
        message = str(exc.value)
        assert "does not accept a `dimensions` parameter" in message
        assert "--dimensions 1536" in message

    def test_a_width_the_model_does_not_produce_is_refused(self) -> None:
        with pytest.raises(UnsupportedDimensionsError, match="3072, 1536, 1024, 256"):
            validate_dimensions(provider="azure", model="text-embedding-3-large", dimensions=999)

    def test_a_supported_truncation_passes(self) -> None:
        validate_dimensions(provider="azure", model="text-embedding-3-large", dimensions=1536)

    def test_ada_002_gets_no_dimensions_on_the_wire(self) -> None:
        """It 400s on every call otherwise, with an SDK error that names
        neither the setting nor the fix."""
        embedder = FlycanonAzureEmbedder(
            "text-embedding-ada-002",
            1536,
            azure_endpoint=_ENDPOINT,
            api_version="2026-05-01",
            api_key="k",
        )
        assert embedder._send_dimensions is False

    def test_the_3_series_does_get_dimensions(self) -> None:
        embedder = FlycanonAzureEmbedder(
            "text-embedding-3-large",
            1536,
            azure_endpoint=_ENDPOINT,
            api_version="2026-05-01",
            api_key="k",
        )
        assert embedder._send_dimensions is True


class TestPrices:
    def test_a_known_model_prices_with_a_named_basis(self) -> None:
        price = price_for(provider="azure", model="text-embedding-3-large")
        assert price is not None
        assert price.usd_per_mtok == 0.13
        assert price.basis == "2026-09-azure-published"

    def test_an_azure_deployment_name_resolves_to_its_model(self) -> None:
        price = price_for(provider="azure", model="prod-text-embedding-3-small")
        assert price is not None and price.usd_per_mtok == 0.02

    def test_an_unknown_model_is_unknown_and_never_zero(self) -> None:
        """``_PRICE_PER_M.get(m, (0, 0))`` on the answer path means a new
        deployment reports free inference. This is the shape that does not."""
        assert price_for(provider="azure", model="prod-emb") is None
        rendered = render_cost(provider="azure", model="prod-emb", tokens=5_000_000)
        assert "cost unknown" in rendered
        assert "$0.00" not in rendered

    def test_a_local_embedder_is_free_as_a_fact_not_as_an_absence(self) -> None:
        price = price_for(provider="ollama", model="nomic-embed-text")
        assert price is not None and price.basis == "local"
        assert "local embedder" in render_cost(provider="ollama", model="nomic", tokens=10_000)

    def test_the_estimate_is_per_million_tokens(self) -> None:
        price = price_for(provider="openai", model="text-embedding-3-large")
        assert price is not None
        assert price.estimate_usd(1_000_000) == pytest.approx(0.13)
        assert "$0.0065" in render_cost(provider="openai", model="text-embedding-3-large", tokens=50_000)

    def test_azure_openai_is_the_same_price_table_as_azure(self) -> None:
        assert price_for(provider="azure-openai", model="text-embedding-3-large") == price_for(
            provider="azure", model="text-embedding-3-large"
        )
