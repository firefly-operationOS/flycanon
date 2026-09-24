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

"""What each embedding model will and will not accept.

Two facts per model, both of which flycanon used to discover at runtime from a
provider error:

* **native width** -- what the model returns when no width is requested. A
  ``--dimensions`` that a model cannot produce is worth refusing in the
  preflight rather than after the first API call of a re-embed.
* **``dimensions`` parameter support** -- OpenAI's ``text-embedding-3-*`` line
  supports Matryoshka truncation and takes the parameter; ``ada-002`` does
  not and returns ``400`` when it is sent. flycanon forwarded it whenever it
  was set, so ``azure:text-embedding-ada-002`` failed on every call with an
  error from the SDK that named neither the setting nor the fix.

The table is deliberately small and keyed by the BARE model name, matched by
suffix. On Azure the configured id is a DEPLOYMENT name, which an operator is
free to call anything -- ``azure:prod-emb-large`` resolves to nothing here and
falls through to :data:`UNKNOWN`, which permits everything and asserts
nothing. Guessing would be worse than not knowing: a capability table that
refuses a legitimate deployment because of its name is a table that has to be
worked around.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingModelCapabilities:
    """What one embedding model accepts."""

    #: Width with no ``dimensions`` parameter. ``None`` when not known.
    native_dimensions: int | None
    #: Whether the model accepts ``dimensions=`` at all.
    supports_dimensions_param: bool
    #: Widths the model is documented to produce, empty when unconstrained.
    supported_dimensions: tuple[int, ...] = ()

    def accepts(self, dimensions: int) -> bool:
        if not self.supported_dimensions:
            return True
        return dimensions in self.supported_dimensions


#: The fallback for any model this table does not recognise: no assertion.
UNKNOWN = EmbeddingModelCapabilities(native_dimensions=None, supports_dimensions_param=True)

_CAPABILITIES: dict[str, EmbeddingModelCapabilities] = {
    # OpenAI / Azure OpenAI. The 3-series is Matryoshka-trained, so a shorter
    # width is a truncation of the same vector rather than a different model.
    "text-embedding-3-small": EmbeddingModelCapabilities(
        native_dimensions=1536,
        supports_dimensions_param=True,
        supported_dimensions=(1536, 1024, 512, 256),
    ),
    "text-embedding-3-large": EmbeddingModelCapabilities(
        native_dimensions=3072,
        supports_dimensions_param=True,
        supported_dimensions=(3072, 1536, 1024, 256),
    ),
    "text-embedding-ada-002": EmbeddingModelCapabilities(
        native_dimensions=1536,
        supports_dimensions_param=False,
        supported_dimensions=(1536,),
    ),
    # Ollama's default local embedder -- what the dworkers dev stack runs.
    "nomic-embed-text": EmbeddingModelCapabilities(
        native_dimensions=768,
        supports_dimensions_param=False,
        supported_dimensions=(768,),
    ),
    "mxbai-embed-large": EmbeddingModelCapabilities(
        native_dimensions=1024,
        supports_dimensions_param=False,
        supported_dimensions=(1024,),
    ),
}


def capabilities_for(model: str) -> EmbeddingModelCapabilities:
    """Look up ``model``, matching the bare name as a suffix of a deployment id.

    ``text-embedding-3-large``, ``my-text-embedding-3-large`` and
    ``text-embedding-3-large-2`` all resolve to the 3-large row; a deployment
    named ``prod-emb`` resolves to :data:`UNKNOWN`.
    """
    bare = model.strip().lower()
    bare = bare.split(":", 1)[-1]
    if bare in _CAPABILITIES:
        return _CAPABILITIES[bare]
    for name, capabilities in _CAPABILITIES.items():
        if name in bare:
            return capabilities
    return UNKNOWN


class UnsupportedDimensionsError(ValueError):
    """Raised when a model cannot produce the requested width."""


def validate_dimensions(*, provider: str, model: str, dimensions: int) -> None:
    """Refuse a width the model is known not to produce, naming the way out.

    A model with no row in the table permits everything: an unknown deployment
    name is not evidence of anything.
    """
    capabilities = capabilities_for(model)
    if capabilities.native_dimensions is None:
        return
    if not capabilities.supports_dimensions_param and dimensions != capabilities.native_dimensions:
        raise UnsupportedDimensionsError(
            f"{provider}:{model} does not accept a `dimensions` parameter and always returns "
            f"{capabilities.native_dimensions}; {dimensions} was requested. Use "
            f"--dimensions {capabilities.native_dimensions}, or choose a model that supports "
            "Matryoshka truncation (text-embedding-3-small / -3-large)."
        )
    if not capabilities.accepts(dimensions):
        widths = ", ".join(str(d) for d in capabilities.supported_dimensions)
        raise UnsupportedDimensionsError(
            f"{provider}:{model} produces {widths} dimensions; {dimensions} was requested."
        )
