# Copyright 2026 Firefly Software Solutions Inc
"""RFC 7807 problem-details DTO.

The pyfly default exception advice already produces a similar shape;
flycanon's :class:`ProblemDetails` adds two stable fields the SDKs
rely on:

* ``code``       -- machine-readable, snake-case identifier. Callers
                    branch on this instead of parsing ``detail``.
* ``extensions`` -- free-form dict for problem-specific context (e.g.
                    ``source_id`` on a ``source_not_found`` problem).

The wire contract follows RFC 7807; the rest of the field set is
inherited from FastAPI's idiomatic error shape so existing tooling
keeps working.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProblemDetails(BaseModel):
    """RFC 7807 problem document."""

    type: str = Field(
        default="about:blank",
        description="URI that identifies the problem type.",
        examples=["https://flycanon.dev/problems/source-not-found"],
    )
    title: str = Field(description="Short, human-readable summary.")
    status: int = Field(ge=400, le=599, description="HTTP status code.")
    code: str = Field(
        description="Stable, machine-readable identifier for the failure mode.",
        examples=["source_not_found"],
    )
    detail: str | None = Field(default=None, description="Explanation specific to this occurrence.")
    instance: str | None = Field(
        default=None,
        description="URI reference identifying the specific occurrence of the problem.",
    )
    extensions: dict[str, Any] | None = Field(
        default=None,
        description="Free-form structured context for callers (problem-specific).",
    )
