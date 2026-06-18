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

"""The restricted builtins whitelist for the RLM ``exec`` namespace.

A standalone, dependency-free leaf module so both the in-process engine
(:mod:`flycanon.core.services.query.rlm.session`) and the out-of-process sandbox
child (:mod:`flycanon.core.services.query.rlm.sandbox.runner`) import the *same*
whitelist without either pulling in the other's dependencies. The child in
particular must stay import-light (no ``httpx``/config) -- it holds no secrets
and does no network.

The whitelist is text-processing only: no ``open``, ``import``, ``__import__``,
``eval``, ``exec``, ``compile``, ``input``, or ``globals``.
"""

from __future__ import annotations

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
