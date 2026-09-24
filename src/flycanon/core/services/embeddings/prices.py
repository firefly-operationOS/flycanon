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

"""Embedding prices, keyed by ``(provider, model)`` with a named basis.

Two rules, both learned from the answer path's price table, which keys rows by
a bare model id and returns ``(0, 0)`` for anything it does not recognise:

1. **A row carries its basis.** ``2026-09-azure-published`` is a claim an
   operator can check against a bill. A bare float is a number nobody can
   audit, and the control plane's own catalogue (``cp.model_catalogue``) keys
   its rows this way, so flycanon cannot reconcile against it otherwise.
2. **An unknown model is UNKNOWN, never free.** ``_PRICE_PER_M.get(m, (0, 0))``
   means a new deployment writes cost rows of exactly zero and every billing
   surface reports free inference, silently. :func:`price_for` returns
   ``None`` and every caller is obliged to render "cost unknown" rather than
   "$0.00".

Prices are USD per million input tokens. Embeddings have no output tokens.
A local embedder is priced at exactly zero with the basis ``local``, which is
a fact, not an absence -- the distinction between "free" and "unknown" is the
whole point of this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmbeddingPrice:
    """USD per million input tokens, and where the number came from."""

    usd_per_mtok: float
    basis: str

    def estimate_usd(self, tokens: int) -> float:
        return tokens / 1_000_000 * self.usd_per_mtok


#: Keyed by ``(provider, bare model name)``. Azure rows are the published
#: pay-as-you-go rates; a deployment on a committed-throughput (PTU) contract
#: pays differently and should override the basis in its own runbook rather
#: than trust this table for a forecast.
_PRICES: dict[tuple[str, str], EmbeddingPrice] = {
    ("openai", "text-embedding-3-small"): EmbeddingPrice(0.02, "2026-09-openai-published"),
    ("openai", "text-embedding-3-large"): EmbeddingPrice(0.13, "2026-09-openai-published"),
    ("openai", "text-embedding-ada-002"): EmbeddingPrice(0.10, "2026-09-openai-published"),
    ("azure", "text-embedding-3-small"): EmbeddingPrice(0.02, "2026-09-azure-published"),
    ("azure", "text-embedding-3-large"): EmbeddingPrice(0.13, "2026-09-azure-published"),
    ("azure", "text-embedding-ada-002"): EmbeddingPrice(0.10, "2026-09-azure-published"),
    ("cohere", "embed-v4.0"): EmbeddingPrice(0.12, "2026-09-cohere-published"),
    ("voyage", "voyage-3"): EmbeddingPrice(0.06, "2026-09-voyage-published"),
    ("mistral", "mistral-embed"): EmbeddingPrice(0.10, "2026-09-mistral-published"),
}

#: Providers that run on hardware the deployment already pays for. Zero is the
#: measured price of an API call to a sidecar, not a missing row.
_LOCAL_PROVIDERS = frozenset({"ollama"})


def price_for(*, provider: str, model: str) -> EmbeddingPrice | None:
    """The price row for an embedder, or ``None`` when it is not known.

    ``None`` is the answer a caller must render as ``cost unknown``. On Azure
    the model id is a DEPLOYMENT name, so the lookup also matches a known
    model name appearing inside it -- ``prod-text-embedding-3-large`` prices
    as 3-large, and ``prod-emb`` prices as unknown, which is correct in both
    directions.
    """
    p = provider.strip().lower()
    if p == "azure-openai":
        p = "azure"
    if p in _LOCAL_PROVIDERS:
        return EmbeddingPrice(0.0, "local")
    bare = model.strip().lower()
    exact = _PRICES.get((p, bare))
    if exact is not None:
        return exact
    for (known_provider, known_model), price in _PRICES.items():
        if known_provider == p and known_model in bare:
            return price
    logger.warning(
        "no price row for embedding model %s:%s -- cost is reported as unknown, not as zero. "
        "Add a row to flycanon.core.services.embeddings.prices to make it estimable.",
        provider,
        model,
    )
    return None


def render_cost(*, provider: str, model: str, tokens: int) -> str:
    """One line an operator can read: the money, or the honest absence of it."""
    price = price_for(provider=provider, model=model)
    if price is None:
        return f"cost unknown (no price row for {provider}:{model})"
    if price.basis == "local":
        return "$0.00 (local embedder, no API cost)"
    return f"${price.estimate_usd(tokens):.4f} (basis {price.basis}, ${price.usd_per_mtok:.2f}/Mtok)"
