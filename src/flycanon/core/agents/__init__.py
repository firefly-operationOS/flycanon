# Copyright 2026 Firefly Software Solutions Inc
"""Shared agent-construction utilities.

Every stage in flycanon that talks to an LLM (consolidation, answer)
goes through :func:`build_agent` so a single place owns:

* the output-token budget (avoid Anthropic / OpenAI's default 4096
  truncating structured outputs mid-array);
* future cross-cutting concerns (middleware stack, retry policy,
  metrics emission, ...).

The helper mirrors flyradar/flydocs's equivalent so any flycanon
operator who has tuned one of those services finds the same knob in
the same shape here.
"""

from __future__ import annotations

from flycanon.core.agents.builder import build_agent, resolve_max_output_tokens

__all__ = ["build_agent", "resolve_max_output_tokens"]
