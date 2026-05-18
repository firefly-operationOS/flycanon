# Copyright 2026 Firefly Software Solutions Inc
"""Tiny YAML+Jinja2 prompt loader.

flycanon's prompts live as YAML files under
``flycanon/resources/prompts``. Each file has two keys, ``system``
and ``user``; both can use Jinja2 expressions. The loader compiles
the templates once at startup and exposes a :meth:`render` method
the consolidator calls per request.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined


@dataclass(slots=True)
class PromptTemplate:
    system: str
    user_template: str
    name: str = ""

    def render(self, **variables: Any) -> tuple[str, str]:
        env = Environment(
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )
        system_text = env.from_string(self.system).render(**variables)
        user_text = env.from_string(self.user_template).render(**variables)
        return system_text, user_text


def load_prompt(name: str) -> PromptTemplate:
    """Load ``flycanon/resources/prompts/<name>.yaml`` into a template."""
    resource = files("flycanon.resources.prompts").joinpath(f"{name}.yaml")
    data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    return PromptTemplate(
        system=str(data.get("system") or ""),
        user_template=str(data.get("user") or ""),
        name=name,
    )
