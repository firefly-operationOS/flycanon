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

"""Coverage for the regex PII scanner + redaction + builder."""

from __future__ import annotations

import pytest

from flycanon.core.services.pii.scanner import (
    PiiPolicy,
    RegexPiiScanner,
    build_pii_scanner,
    redact,
)


class TestRegexPatterns:
    def test_detects_email(self):
        findings = RegexPiiScanner().scan("contact me at alice@example.com please")
        kinds = {f.kind for f in findings}
        assert "email" in kinds

    def test_detects_us_ssn(self):
        findings = RegexPiiScanner().scan("SSN: 123-45-6789 on file")
        assert any(f.kind == "us_ssn" for f in findings)

    def test_detects_credit_card(self):
        findings = RegexPiiScanner().scan("card: 4111 1111 1111 1111")
        assert any(f.kind == "credit_card" for f in findings)

    def test_detects_iban(self):
        findings = RegexPiiScanner().scan("transfer to ES9121000418450200051332 today")
        assert any(f.kind == "iban" for f in findings)

    def test_clean_text_returns_empty(self):
        findings = RegexPiiScanner().scan("nothing suspicious here at all")
        assert findings == []


class TestRedaction:
    def test_redact_replaces_findings(self):
        scanner = RegexPiiScanner()
        text = "email alice@example.com and ssn 123-45-6789 are sensitive"
        findings = scanner.scan(text)
        redacted = redact(text, findings)
        assert "alice@example.com" not in redacted
        assert "123-45-6789" not in redacted
        assert "[REDACTED:EMAIL]" in redacted
        assert "[REDACTED:US_SSN]" in redacted

    def test_redact_empty_text_returns_unchanged(self):
        assert redact("", []) == ""

    def test_redact_no_findings_returns_unchanged(self):
        assert redact("safe text", []) == "safe text"


class TestBuilder:
    def test_default_is_regex(self):
        assert isinstance(build_pii_scanner("regex"), RegexPiiScanner)

    def test_disabled_returns_none(self):
        assert build_pii_scanner("disabled") is None

    def test_unknown_falls_back_to_regex(self):
        assert isinstance(build_pii_scanner("unknown"), RegexPiiScanner)

    def test_presidio_falls_back_to_regex(self):
        # Presidio adapter ships in a follow-up; the builder logs a
        # warning and returns the regex scanner.
        assert isinstance(build_pii_scanner("presidio"), RegexPiiScanner)


class TestPolicyEnum:
    def test_known_policies_are_constructible(self):
        assert PiiPolicy("warn") == PiiPolicy.warn
        assert PiiPolicy("redact") == PiiPolicy.redact
        assert PiiPolicy("reject") == PiiPolicy.reject
        assert PiiPolicy("disabled") == PiiPolicy.disabled

    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError):
            PiiPolicy("nope")
