"""Tests for per-scenario markdown writeup generation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sbe_cte_bench.reporting.markdown import render_scenario_writeup


def _make_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": "S03-k4-test",
        "timestamp": datetime(2026, 4, 25, tzinfo=UTC).isoformat(),
        "scenario": "S03",
        "scenario_title": "Boundary tax",
        "variant": {"boundary_position": 4, "scale_factor": "SF1"},
        "host": {
            "kernel": "6.8",
            "cpu_model": "EPYC",
            "physical_cores": 16,
            "memory_gb": 128,
            "storage": "NVMe",
        },
        "mongo": {
            "version": "8.2.2",
            "framework_control": "trySbeEngine",
            "wt_cache_gb": 1.5,
            "pipeline": [],
            "explain": {},
            "spill": {},
            "timings_ms": [142.0],
            "median_ms": 142.0,
            "p95_ms": 152.0,
            "p99_ms": 153.0,
            "min_ms": 138.0,
            "max_ms": 155.0,
            "iqr_ms": 6.0,
            "cv": 0.04,
            "n": 1,
            "p99_low_confidence": True,
            "errors": [],
        },
        "oracle": {
            "version": "26.0.0.0",
            "sga_mb": 1200,
            "pga_mb": 600,
            "sql": "WITH ...",
            "plan": {},
            "workarea": {},
            "statspack": {},
            "timings_ms": [38.9],
            "median_ms": 38.9,
            "p95_ms": 41.0,
            "p99_ms": 41.5,
            "min_ms": 38.0,
            "max_ms": 42.0,
            "iqr_ms": 0.8,
            "cv": 0.02,
            "n": 1,
            "p99_low_confidence": True,
            "errors": [],
        },
        "equivalence": {
            "mongo_hash": "f47ac10b58cc4372a567",
            "oracle_hash": "f47ac10b58cc4372a567",
            "match": True,
            "row_count_mongo": 5000,
            "row_count_oracle": 5000,
        },
        "prediction": {
            "claim": "Mongo at boundary_position=4 ratio >= 3.5",
            "expected": {"metric": "ratio", "operator": ">=", "value": 3.5},
            "observed": {"metric": "ratio", "value": 3.65},
            "pass": True,
        },
    }


@pytest.mark.unit
def test_writeup_contains_scenario_title() -> None:
    text = render_scenario_writeup(_make_record())
    assert "Boundary tax" in text


@pytest.mark.unit
def test_writeup_contains_observed_ratio() -> None:
    text = render_scenario_writeup(_make_record())
    assert "3.65" in text


@pytest.mark.unit
def test_writeup_contains_pass_verdict() -> None:
    text = render_scenario_writeup(_make_record())
    # Must contain a clear pass/fail signal.
    assert "PASS" in text or "passed" in text.lower()


@pytest.mark.unit
def test_writeup_includes_equivalence_match_status() -> None:
    text = render_scenario_writeup(_make_record())
    assert "equivalence" in text.lower()


@pytest.mark.unit
def test_writeup_failed_prediction_marked() -> None:
    record = _make_record()
    record["prediction"]["pass"] = False
    record["prediction"]["observed"] = {"metric": "ratio", "value": 1.2}
    text = render_scenario_writeup(record)
    assert "FAIL" in text or "failed" in text.lower()


@pytest.mark.unit
def test_writeup_includes_per_engine_medians() -> None:
    text = render_scenario_writeup(_make_record())
    assert "142" in text  # mongo median
    assert "38.9" in text  # oracle median
