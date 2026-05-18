# Copyright 2026 Firefly Software Solutions Inc
"""Coverage for the quality detectors' pure helpers.

The full ConflictDetector flow requires an LLM call; we exercise
the cosine + caching logic and the stale-score response shape
here. End-to-end LLM behaviour is exercised live in the docker
stack.
"""

from __future__ import annotations

from flycanon.core.services.quality.conflict_detector import _cosine as cdc_cosine
from flycanon.core.services.quality.stale_detector import (
    _cosine as sdc_cosine,
)
from flycanon.core.services.quality.stale_detector import (
    _empty_score,
    _is_fresh,
)


class TestCosine:
    def test_orthogonal_vectors_return_zero(self):
        assert sdc_cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
        assert cdc_cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_identical_vectors_return_one(self):
        assert abs(sdc_cosine([1.0, 1.0], [1.0, 1.0]) - 1.0) < 1e-9
        assert abs(cdc_cosine([1.0, 1.0], [1.0, 1.0]) - 1.0) < 1e-9

    def test_zero_vector_short_circuits(self):
        assert sdc_cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cdc_cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_mismatched_lengths_return_zero(self):
        assert sdc_cosine([1.0], [1.0, 2.0]) == 0.0


class TestStaleScoreHelpers:
    def test_empty_score_has_null_score(self):
        es = _empty_score()
        assert es["score"] is None
        assert es["sample_size"] == 0

    def test_is_fresh_with_missing_computed_at(self):
        assert _is_fresh({}) is False

    def test_is_fresh_with_invalid_timestamp(self):
        assert _is_fresh({"computed_at": "not-a-date"}) is False
