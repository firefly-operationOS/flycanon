# Copyright 2026 Firefly Software Solutions Inc
"""PII detection + redaction for ingest-time content.

The canon stores extracted text from arbitrary documents; HR /
legal sources often carry SSNs, names, salaries, etc. Without a
guardrail, those land in pgvector and any RAG query can surface
them -- a non-starter under GDPR / CCPA / HIPAA.

This module exposes a tiny :class:`PiiScanner` protocol with a
default regex-based implementation that covers the high-value
patterns (email, US SSN, phone, credit card). A future commit
plugs Microsoft Presidio behind the same protocol for richer
NER-driven detection.

Three policies are supported via :class:`PiiPolicy`:

* ``warn``    -- the source row's ``metadata.pii_findings`` carries
                the matches; chunks are indexed as-is. Default.
* ``redact``  -- each hit is rewritten as ``[REDACTED:<KIND>]`` in
                the chunk text before embedding + indexing.
* ``reject``  -- the intake fails with ``error_code=pii_detected``
                and the source row's status is ``failed``.

The scanner runs once over the loader's emitted text per source.
For multi-artifact intakes (archives, emails) it sees the merged
Markdown document so a contaminated child artefact triggers the
right policy on the whole intake.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

logger = logging.getLogger(__name__)


class PiiPolicy(StrEnum):
    warn = "warn"
    redact = "redact"
    reject = "reject"
    # Treated as "no-op" -- the IntakeService doesn't call the
    # scanner at all when disabled.
    disabled = "disabled"


class PiiPolicyViolation(Exception):
    """Raised when ``pii_policy=reject`` and findings exist."""

    code = "pii_detected"
    http_status = 422

    def __init__(self, findings: list[PiiFinding]) -> None:
        kinds = sorted({f.kind for f in findings})
        super().__init__(f"pii detected: {kinds}")
        self.findings = findings


@dataclass(frozen=True, slots=True)
class PiiFinding:
    """One PII hit in the scanned text."""

    kind: str
    start: int
    end: int
    snippet: str


class PiiScanner(Protocol):
    """Detect + (optionally) redact PII in a single text blob."""

    def scan(self, text: str) -> list[PiiFinding]: ...


# Patterns are intentionally conservative -- they err on the side
# of false negatives over false positives so the warn policy stays
# usable on real corpora. Operators that need higher recall flip
# the scanner to ``presidio``.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(
            r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
        ),
    ),
    (
        "us_ssn",
        re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    ),
    (
        "phone",
        # International + US formats; conservative on length.
        re.compile(
            r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\d)"
        ),
    ),
    (
        "credit_card",
        # Luhn-friendly 13-19 digit groupings with optional dashes /
        # spaces. The scanner does NOT validate the checksum --
        # warn-first wins over completeness for v1.
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b"),
    ),
    (
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    ),
)


class RegexPiiScanner:
    """Default scanner -- pattern-based, no dependencies.

    Skips bare phone matches that look like ordinary years (4
    digits) by deferring to the wider grouping rules above; the
    default patterns are deliberately conservative to keep the
    ``warn`` policy noise low on real business docs.
    """

    def scan(self, text: str) -> list[PiiFinding]:
        if not text:
            return []
        findings: list[PiiFinding] = []
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)
                # Filter obviously-bad phone hits (e.g. years).
                if kind == "phone" and len(re.sub(r"\D", "", snippet)) < 7:
                    continue
                findings.append(
                    PiiFinding(
                        kind=kind,
                        start=match.start(),
                        end=match.end(),
                        snippet=snippet,
                    )
                )
        return findings


def redact(text: str, findings: list[PiiFinding]) -> str:
    """Replace every finding with ``[REDACTED:<KIND>]``.

    The replacements are applied right-to-left so the positions
    encoded in the findings remain valid as we mutate the string.
    """
    if not findings or not text:
        return text
    pieces: list[str] = []
    cursor = 0
    for finding in sorted(findings, key=lambda f: f.start):
        if finding.start < cursor:
            # Overlapping match -- skip; the wider replacement wins.
            continue
        pieces.append(text[cursor : finding.start])
        pieces.append(f"[REDACTED:{finding.kind.upper()}]")
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def build_pii_scanner(name: str) -> PiiScanner | None:
    """Resolve the configured scanner from ``FLYCANON_PII_SCANNER``.

    Recognised values: ``regex`` (default), ``presidio`` (planned),
    ``disabled``. Unknown values fall back to the regex scanner so
    a typo never silently disables the guardrail.
    """
    key = (name or "regex").strip().lower()
    if key == "disabled":
        return None
    if key == "regex":
        return RegexPiiScanner()
    if key == "presidio":
        # Adapter intentionally unimplemented in v1 -- ships in a
        # follow-up once the dependency footprint is reviewed.
        logger.warning(
            "presidio scanner not available in this build -- falling back to regex"
        )
        return RegexPiiScanner()
    logger.warning("unknown pii scanner %r -- falling back to regex", name)
    return RegexPiiScanner()
